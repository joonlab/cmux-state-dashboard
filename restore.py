"""레이아웃 트리 → cmux 충실 복원.

cmux `new-workspace --layout` 이 스플릿을 네이티브로 지원하므로, 네이티브 레이아웃 트리를
--layout 스키마로 번역해 워크스페이스를 (거의) 단일 호출로 재현한다. 이후 생성된 워크스페이스의
터미널에 claude resume를 바인딩한다(surface resume set — 자동 실행이 아니라 '재개 명령' 저장).

--layout 스키마(cmux):
  split: {"direction":"horizontal"|"vertical","split":<0..1>,"children":[A,B]}
  pane : {"pane":{"surfaces":[{"type":"terminal"|"browser","command"?,"url"?}]}}
"""
import glob
import json
import os
from collections import OrderedDict

import cmux_client
import claude_index
import db
import layout as layout_mod
from config import BACKUP_DIR, PINNED_DIR

MAX_CLAUDE_AUTORUN = 12   # 자동 실행 시 OOM 가드(배치 총합). 유휴 claude 는 가벼워 12 안전.


def corrected_resume(panel):
    """패널의 (resumeCommand, cwd)를 세션의 실제 launch cwd 기준으로 교정해 반환.

    cmux가 저장한 resume 명령의 `cd '<경로>' && ...` 프리픽스는 '바인딩 캡처 순간의
    (drift된) 폴더'라, 이후 그 폴더가 이동/rename되면 cd 실패 → `&&` 단락 → 뒤의
    cwd-fix wrapper 조차 미실행 → resume가 깨진다. 세부 교정 로직은 claude_index.
    """
    return claude_index.correct_resume(
        panel.get("sessionId"), panel.get("resumeCommand"), panel.get("cwd"))


def _panel_sids(layout_windows):
    return {p["sessionId"]
            for w in layout_windows for ws in w["workspaces"]
            for p in ws["panels"].values() if p.get("sessionId")}


def load_layout_windows(snap):
    """DB 스냅샷 → layoutWindows.

    신규 스냅샷은 normalized 에 layoutWindows 가 들어있다. 구(舊) 스냅샷(이 기능 이전)에는
    없으므로, 세션 집합이 가장 많이 겹치는 네이티브 백업(data/backups/session_*.json)을 파싱해
    복원한다(타임스탬프보다 견고 — 리셋 경계의 빈 백업은 0점이라 선택 안 됨).
    """
    norm = snap.get("normalized") or {}
    lw = norm.get("layoutWindows")
    if lw:
        return _with_tree_only(lw, norm)
    want = {s["sessionId"] for s in norm.get("sessions", [])}
    paths = (sorted(glob.glob(os.path.join(PINNED_DIR, "*.json")))
             + sorted(glob.glob(os.path.join(BACKUP_DIR, "session_*.json"))))
    best, best_score = [], -1
    for p in paths:
        try:
            with open(p) as f:
                nat = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        cand = layout_mod.parse_native_layout(nat, claude_index.session_meta)
        score = len(want & _panel_sids(cand))
        if score > best_score:
            best, best_score = cand, score
    # 매칭된 레이아웃을 DB 스냅샷에 memoize → 다음 조회는 즉시 + 백업 회전에도 영구 보존.
    if best and best_score > 0 and snap.get("id"):
        try:
            norm["layoutWindows"] = best
            db.update_snapshot_normalized(snap["id"], norm)
        except Exception:
            pass
    return _with_tree_only(best, norm)


def _with_tree_only(layout_windows, norm):
    """네이티브 레이아웃 + tree 에만 있는 창(합성).

    ⚠️ memoize **이후**에 붙인다 — 합성분은 근사값이므로 DB(복원 원천)에 섞지 않는다.
    읽기 시점에 만들기 때문에 과거 스냅샷에도 그대로 소급 적용된다.
    """
    lw = list(layout_windows or [])
    try:
        lw += layout_mod.synthesize_tree_windows(
            norm.get("windows") or [], lw, claude_index.session_meta)
    except Exception:
        pass      # 합성 실패가 네이티브 복원을 막아서는 안 된다
    return lw


def _selected(panel_ids, selected):
    return [pid for pid in panel_ids if selected is None or pid in selected]


def translate(node, panels, selected, autorun, acc_terms, budget, running_sids=None):
    """레이아웃 트리 → cmux --layout 노드(선택분만). 빈 노드는 None.

    acc_terms: 재현되는 터미널 panelInfo를 트리 순서대로 누적(사후 resume 바인딩 매칭용).
    budget: {"n":남은 autorun 실행 수} — command 삽입은 이 예산 내에서만.
    running_sids: 이미 실행중인 세션 id 집합(소문자). 여기 있는 sid 는 command 를 주입하지 않는다
        (이미 살아있는 세션을 두 번째로 --resume 하면 같은 jsonl 에 claude 2개 = 충돌·이중기동).
        주입한 sid 는 이 집합에 추가돼, 한 복원 내 중복 워크스페이스(오염 스냅샷)의 재주입도 막는다.
    """
    if running_sids is None:   # 참조 보존(빈 set 도 그대로 써야 주입분 누적이 상위로 전파됨)
        running_sids = set()
    if not node:
        return None
    if node["type"] == "pane":
        surfaces = []
        for pid in _selected(node.get("panelIds", []), selected):
            p = panels.get(pid)
            if not p:
                continue
            if p["type"] == "browser":
                s = {"type": "browser"}
                if p.get("url"):
                    s["url"] = p["url"]
                surfaces.append(s)
            else:  # terminal / claude
                s = {"type": "terminal"}
                sid_l = (p.get("sessionId") or "").lower()
                do_ar = bool(autorun and p.get("resumeCommand") and budget["n"] > 0
                             and not (sid_l and sid_l in running_sids))  # 이미 실행중이면 주입 안 함
                if do_ar:
                    # autorun = 레이아웃 terminal command 로 실행. cmux 는 워크스페이스 **생성 시점**에
                    #   배경(in_window=false·미실현) 워크스페이스에서도 이 command 를 실행한다
                    #   (2026-07-23 실증: --focus false 로 만든 미실현 ws 3개의 command 전부 실행됨).
                    #   ⚠️ 과거엔 여기서 command 를 빼고 "생성 후 select+send" 후처리 패스로 돌렸는데,
                    #   다중 복원 시 실현/포커스 경쟁으로 send 가 유실돼 claude 가 안 떴다("레이아웃
                    #   command 가 유실된다"는 이전 진단은 오진). → 안정적 경로인 레이아웃 command 로 복귀.
                    cmd, _cwd = corrected_resume(p)
                    if cmd:
                        s["command"] = cmd
                        budget["n"] -= 1
                        if sid_l:
                            running_sids.add(sid_l)   # 같은 복원 내 중복 재주입 방지
                surfaces.append(s)
                acc_terms.append((p, do_ar))   # (panelInfo, autorun여부)
        return {"pane": {"surfaces": surfaces}} if surfaces else None
    # split
    first = translate(node.get("first"), panels, selected, autorun, acc_terms, budget, running_sids)
    second = translate(node.get("second"), panels, selected, autorun, acc_terms, budget, running_sids)
    if first and second:
        return {
            "direction": node.get("orientation") or "horizontal",
            "split": node.get("dividerPosition") or 0.5,
            "children": [first, second],
        }
    return first or second   # 한쪽만 남으면 축소(스플릿 없앰)


def window_default_workspace(window_id):
    """새로 만든 창의 기본(자동 생성) 워크스페이스 ref — 복원 후 비어있으면 닫기용."""
    if not window_id:
        return None
    try:
        t = cmux_client.system_tree()
    except cmux_client.CmuxError:
        return None
    for w in t.get("windows", []):
        if w.get("id") == window_id or w.get("ref") == window_id:
            wss = w.get("workspaces", [])
            if wss:
                return wss[0].get("ref") or wss[0].get("id")
    return None


def _find_ws_node(tree, ws_ref):
    """전체 tree에서 특정 워크스페이스 노드 찾기(ref 또는 id 매칭)."""
    for w in tree.get("windows", []):
        for ws in w.get("workspaces", []):
            if ws.get("ref") == ws_ref or ws.get("id") == ws_ref:
                return ws
    return None


def _ws_terminal_refs(ws_node):
    """워크스페이스 노드 내 터미널 서피스 **UUID**를 pane/surface 순서대로 수집.

    (전체 tree가 아니라 해당 워크스페이스로 스코프 — resume를 남의 터미널에 바인딩하는 사고 방지.)
    ⚠️ ref(surface:N)는 워크스페이스 재배정(group.add) 후 시프트될 수 있어, autorun 최종 send가
       엉뚱한 터미널로 가는 사고가 났다 → **안정적인 UUID(id)** 를 쓴다.
    """
    refs = []
    for p in ws_node.get("panes", []):
        for s in p.get("surfaces", []):
            if s.get("type") == "terminal":
                refs.append(s.get("id") or s.get("ref"))
    return refs


# cmux 워크스페이스 색 이름(customColor 는 #hex; workspace-action 은 이름/‌#hex 모두 허용)
def reconstruct_workspace(ws, window_ref, selected, autorun, budget=None,
                          group=None, group_placement=None, running_sids=None):
    """워크스페이스 하나를 충실 재현. 반환: 결과 dict(ref 포함).

    budget: {"n": 남은 autorun 실행 수} — 배치 전체 공유(OOM 가드). None이면 워크스페이스 단독.
    group: 배정할 워크스페이스 그룹 id(있으면 --group). group_placement: top|end|afterCurrent.
    running_sids: 이미 실행중인 세션 id 집합 — 그 sid 는 command 미주입(이중기동 방지).
    """
    acc_terms = []
    if budget is None:
        budget = {"n": MAX_CLAUDE_AUTORUN if autorun else 0}
    layout_node = translate(ws.get("layout"), ws.get("panels", {}),
                            selected, autorun, acc_terms, budget, running_sids)
    title = ws.get("title") or "restored"
    if not layout_node:
        return {"ok": False, "workspace": title, "error": "선택된 서피스 없음"}

    layout_json = json.dumps(layout_node, ensure_ascii=False)
    try:
        # focus=True 필수: 백그라운드(focus=false)로 만들면 cmux가 서피스를 창에 부착(realize)하지
        # 않아 "빈 창"처럼 안 열린다. 포커스=생성+선택+창 전면화로 서피스가 즉시 렌더된다.
        _out, ref = cmux_client.new_workspace(
            name=title, description=ws.get("description") or None,
            cwd=ws.get("cwd") or None, layout=layout_json, window=window_ref, focus=True,
            group=group, group_placement=group_placement)
    except cmux_client.CmuxError as e:
        # flat 폴백: 스플릿 없이 한 페인에 전체 서피스 나열
        return _flat_fallback(ws, window_ref, selected, autorun, str(e), running_sids)

    if not ref:
        return {"ok": False, "workspace": title, "error": "워크스페이스 ref 파싱 실패"}

    # 색/고정 적용(best-effort)
    if ws.get("color"):
        try:
            cmux_client.workspace_action(ref, "set-color", color=ws["color"])
        except cmux_client.CmuxError:
            pass
    if ws.get("pinned"):
        try:
            cmux_client.workspace_action(ref, "pin")
        except cmux_client.CmuxError:
            pass

    # resume 바인딩(메타데이터) — autorun 실행 여부와 무관하게 모든 resume 터미널에 심는다.
    #   실행은 레이아웃 command(translate)가 생성 시점에 담당하고, 여기선 cmux 세션JSON 에 남을
    #   바인딩만 설정한다: (1) 미래 스냅샷의 resumeCommand 정확도, (2) 상한 초과분(command 미주입)의
    #   수동 복원 메타데이터. ⚠️ 바인딩 자체는 자동 실행되지 않는다("stored for inspection/manual").
    bound = 0
    resume_terms = [t for t in acc_terms if t[0].get("resumeCommand")]
    if resume_terms:
        try:
            tree = cmux_client.system_tree()
            wsnode = _find_ws_node(tree, ref)
            term_refs = _ws_terminal_refs(wsnode) if wsnode else []
        except cmux_client.CmuxError:
            term_refs = []
        # acc_terms 순서 == 생성 순서 == 트리 순서 가정. 터미널만 zip.
        term_iter = iter(term_refs)
        for p, _do_ar in acc_terms:
            sref = next(term_iter, None)
            if sref is None:
                break
            if not p.get("resumeCommand"):
                continue
            fixed_cmd, fixed_cwd = corrected_resume(p)
            try:
                cmux_client.surface_resume_set(
                    sref, shell=fixed_cmd, cwd=fixed_cwd,
                    kind="agent", checkpoint=p.get("sessionId"),
                    name=(p.get("label") or p.get("title") or None))
                bound += 1
            except cmux_client.CmuxError:
                pass

    # 서피스 부착 보장(insurance): 워크스페이스 선택 → 창에 realize.
    try:
        cmux_client.select_workspace(ref)
    except cmux_client.CmuxError:
        pass

    return {"ok": True, "workspace": title, "ref": ref, "mode": "충실 재현",
            "surfaces": _count_surfaces(layout_node), "resumeBound": bound}


def reconstruct_group(group_def, members, window_ref, autorun, budget, running_sids=None):
    """워크스페이스 그룹 + 멤버들을 재현. members=[(ws, selset), ...] 원래 순서.

    ★ 그룹을 **먼저** 만들고 모든 멤버를 `--group` 으로 생성한다.
    `group.create` 는 자체 anchor 헤더 워크스페이스를 새로 만들기 때문에, 멤버를 먼저 만들면
    그 첫 멤버가 그룹에 편입되지 못하고 그룹 밖에 남는다(2026-07-15 버그).
    그룹은 caller 의 현재 창에 생성되므로, 엔드포인트가 대상 창을 미리 current 로 만들어 둔다.
    반환: (results 리스트, group_id).
    """
    if not members:
        return [], None
    gid = None
    try:
        # 앵커 cwd 를 홈으로 고정: 안 하면 앵커가 프로젝트 cwd 를 물려받아
        # 그룹 `+` 로 여는 새 워크스페이스가 항상 그 프로젝트 폴더에서 열린다.
        gid = cmux_client.group_create(cwd=os.path.expanduser("~"))
        if gid and group_def.get("name"):
            cmux_client.group_rename(gid, group_def["name"])
    except cmux_client.CmuxError:
        gid = None
    results = []
    for ws, selset in members:
        r = reconstruct_workspace(ws, window_ref, selset, autorun, budget,
                                  group=gid, group_placement="end", running_sids=running_sids)
        results.append(r)
    if gid:
        # 멤버 다 넣은 뒤 그룹 속성 적용(접힘은 마지막에).
        for cond, fn in [
            (group_def.get("pinned"), lambda: cmux_client.group_pin(gid)),
            (group_def.get("icon"), lambda: cmux_client.group_set_icon(gid, group_def["icon"])),
            (group_def.get("color"), lambda: cmux_client.group_set_color(gid, group_def["color"])),
            (group_def.get("collapsed"), lambda: cmux_client.group_collapse(gid)),
        ]:
            if cond:
                try:
                    fn()
                except cmux_client.CmuxError:
                    pass
        for r in results:
            if r.get("ok"):
                r["group"] = group_def.get("name")
    return results, gid


def _flat_fallback(ws, window_ref, selected, autorun, err, running_sids=None):
    """스플릿 재현 실패 시: 한 페인에 선택 서피스 전부 탭으로."""
    panels = ws.get("panels", {})
    surfaces, terms = [], []
    budget = MAX_CLAUDE_AUTORUN if autorun else 0
    if running_sids is None:
        running_sids = set()

    def collect(node):
        nonlocal budget
        if not node:
            return
        if node["type"] == "pane":
            for pid in _selected(node.get("panelIds", []), selected):
                p = panels.get(pid)
                if not p:
                    continue
                if p["type"] == "browser":
                    s = {"type": "browser"}
                    if p.get("url"):
                        s["url"] = p["url"]
                    surfaces.append(s)
                else:
                    s = {"type": "terminal"}
                    sid_l = (p.get("sessionId") or "").lower()
                    if (autorun and p.get("resumeCommand") and budget > 0
                            and not (sid_l and sid_l in running_sids)):  # 이미 실행중이면 주입 안 함
                        s["command"], _ = corrected_resume(p); budget -= 1
                        if sid_l:
                            running_sids.add(sid_l)
                    surfaces.append(s); terms.append(p)
        else:
            collect(node.get("first")); collect(node.get("second"))

    collect(ws.get("layout"))
    if not surfaces:
        return {"ok": False, "workspace": ws.get("title"), "error": err}
    layout_json = json.dumps({"pane": {"surfaces": surfaces}}, ensure_ascii=False)
    try:
        _out, ref = cmux_client.new_workspace(
            name=ws.get("title") or "restored", description=ws.get("description") or None,
            cwd=ws.get("cwd") or None, layout=layout_json, window=window_ref, focus=True)
        return {"ok": True, "workspace": ws.get("title"), "ref": ref,
                "mode": "flat 폴백", "surfaces": len(surfaces),
                "note": f"스플릿 재현 실패→flat ({err[:40]})"}
    except cmux_client.CmuxError as e:
        return {"ok": False, "workspace": ws.get("title"), "error": str(e)}


def restore_layout_windows(layout_windows, selections=None, target="new", autorun=True):
    """layoutWindows(+선택 필터) → 실제 cmux 재구성. **대시보드 엔드포인트와 cmux-config 스킬이
    공유하는 단일 오케스트레이션**(여기만 고치면 양쪽 다 반영 — 로직 이원화 금지).

    selections: [{"wsId":str, "panelIds":[str]|None}, ...] · None 이면 전체 워크스페이스·전체 패널.
    target: "new"=새 창 1개에 모음 / "current"=현재 활성 창.
    autorun: claude 자동 실행(레이아웃 command 방식). 배치 상한 MAX_CLAUDE_AUTORUN.
    반환: {"results","workspaces","restored","groups","autorunInjected","doneKeys"}
    ValueError: 복원 대상이 하나도 없을 때.
    """
    ws_lookup, group_lookup = {}, {}
    for w in layout_windows:
        for g in w.get("groups", []):
            group_lookup[(w["index"], g["id"])] = g
        for ws in w["workspaces"]:
            ws_lookup[ws["id"]] = (w["index"], ws)

    if selections is None:   # 전체 복원(그룹 anchor 헤더는 아래 루프에서 제외)
        selections = [{"wsId": wid, "panelIds": None} for wid in ws_lookup]

    by_win, done_keys = OrderedDict(), []
    for sel in selections:
        wsid = sel.get("wsId") if isinstance(sel, dict) else getattr(sel, "wsId", None)
        pids = sel.get("panelIds") if isinstance(sel, dict) else getattr(sel, "panelIds", None)
        if wsid not in ws_lookup:
            continue
        wi, ws = ws_lookup[wsid]
        selset = set(pids) if pids else None
        by_win.setdefault(wi, []).append((ws, selset))
        for pid, p in ws["panels"].items():
            if selset is not None and pid not in selset:
                continue
            if p.get("sessionId"):
                done_keys.append("c:" + p["sessionId"])
            elif p.get("url"):
                done_keys.append("b:" + p["url"])
    if not by_win:
        raise ValueError("복원할 워크스페이스가 없습니다")

    budget = {"n": MAX_CLAUDE_AUTORUN if autorun else 0}
    # 이미 실행중인 세션은 command 미주입(같은 jsonl 에 claude 2개 = 충돌). 시작 전 1회 스냅샷.
    running_sids = cmux_client.running_claude_sids() if autorun else set()
    results, groups_made, group_reconcile = [], 0, []

    default_ws = None
    if target == "current":
        win_ref = cmux_client.current_window_id()
    else:
        try:
            _o, win_ref = cmux_client.new_window()
        except cmux_client.CmuxError:
            win_ref = None
        default_ws = window_default_workspace(win_ref)   # 새 창의 빈 기본 워크스페이스
        # 그룹(group.create)은 caller 의 "현재 창"에 생기므로 새 창을 current 로 만들어 둔다.
        if default_ws:
            try:
                cmux_client.select_workspace(default_ws)
            except cmux_client.CmuxError:
                pass

    for wi, ws_list in by_win.items():
        ws_list.sort(key=lambda t: t[0].get("wsIndex", 0))   # 원래 순서 유지
        grouped, ungrouped = OrderedDict(), []
        for ws, selset in ws_list:
            if ws.get("isGroupAnchor"):
                continue    # "Group N" 자동 헤더는 group.create 가 재생성 → 복원 제외
            gid0 = ws.get("groupId")
            if gid0 and (wi, gid0) in group_lookup:
                grouped.setdefault(gid0, []).append((ws, selset))
            else:
                ungrouped.append((ws, selset))
        for gid0, members in grouped.items():
            try:
                gres, newgid = reconstruct_group(group_lookup[(wi, gid0)], members, win_ref,
                                                 autorun, budget, running_sids=running_sids)
                if newgid:
                    groups_made += 1
                    group_reconcile.append(
                        (newgid, [r.get("ref") for r in gres if r.get("ok") and r.get("ref")]))
            except Exception as e:      # noqa: BLE001 — 한 그룹 실패가 배치를 죽이지 않게
                gres = [{"ok": False, "workspace": m[0].get("title"), "error": str(e)}
                        for m in members]
            results.extend(gres)
        for ws, selset in ungrouped:
            try:
                r = reconstruct_workspace(ws, win_ref, selset, autorun, budget=budget,
                                          running_sids=running_sids)
            except Exception as e:      # noqa: BLE001
                r = {"ok": False, "workspace": ws.get("title"), "error": str(e)}
            results.append(r)

    # 그룹 멤버십 재확정: cmux 멤버십은 사이드바 "위치" 기반이라 연속 생성 시 뒤 그룹 anchor 가
    #   앞 그룹 멤버를 흡수한다 → 전 생성 후 group.add(UUID)로 위치 무관 명시 재배정(2패스).
    if group_reconcile:
        try:
            tree = cmux_client.system_tree()
            ref2uuid = {ws.get("ref"): ws.get("id")
                        for w in tree.get("windows", []) for ws in w.get("workspaces", [])
                        if ws.get("ref")}
        except cmux_client.CmuxError:
            ref2uuid = {}
        for _pass in range(2):
            for gid, refs in group_reconcile:
                for ref in refs:
                    uuid = ref2uuid.get(ref)
                    if uuid:
                        try:
                            cmux_client.group_add(gid, uuid)
                        except cmux_client.CmuxError:
                            pass

    # autorun 실행은 레이아웃 command(translate)가 생성 시점에 담당 — 후처리 send 없음. 집계만.
    autorun_injected = (MAX_CLAUDE_AUTORUN - budget["n"]) if autorun else 0

    # 새 창 모드에서만 빈 기본 워크스페이스 닫기(복원분이 있으면 비-마지막이라 성공).
    if default_ws and any(r.get("ok") for r in results):
        try:
            cmux_client.close_workspace(default_ws)
        except cmux_client.CmuxError:
            pass

    return {"results": results, "workspaces": len(results),
            "restored": sum(1 for r in results if r.get("ok")),
            "groups": groups_made, "autorunInjected": autorun_injected,
            "doneKeys": done_keys}


def _count_surfaces(node):
    if not node:
        return 0
    if "pane" in node:
        return len(node["pane"]["surfaces"])
    return sum(_count_surfaces(c) for c in node.get("children", []))

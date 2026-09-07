#!/usr/bin/env python3
"""cmux 레이아웃 임시 저장(스냅샷) · 복원 · 정리 — cmux-config 스킬.

지금 열려 있는 **창/그룹/워크스페이스/탭(claude·터미널·브라우저)** 상태를 파일로 영구 저장한다.
cmux가 꺼지든 맥이 재부팅되든 파일은 그대로 남으므로, 나중에 목록을 보고 그 상태를 그대로 되살릴 수 있다.
저장 직후 원본을 닫아 CPU/메모리를 회수하는 것도 지원한다(항상 **닫을 목록을 먼저 보여주고** 승인 후 실행).

복원 엔진은 cmux 상태·복구 대시보드의 검증된 구현(`restore.restore_layout_windows`)을 **그대로 재사용**한다
→ 레이아웃 command autorun(배경 워크스페이스도 생성 시 실행) / 그룹 create-first + 위치기반 멤버십
   reconciliation / pinned / resume cwd 드리프트 교정을 전부 상속하고, 버그 수정도 한 곳에서만 하면 된다.

사용:
  cmux-snapshot.py save   [--label 이름] [--note 메모] [--close 범위] [--yes]
  cmux-snapshot.py list   [--limit N]
  cmux-snapshot.py show   <id|latest>
  cmux-snapshot.py restore <id|latest> [--target new|current] [--only 범위] [--no-claude] [--dry-run]
  cmux-snapshot.py close  <범위> [--yes]
  cmux-snapshot.py delete <id>

범위(scope) 문법 — 쉼표로 여러 개:
  all                 저장/라이브의 전부 (★현재 Claude Code 세션 워크스페이스는 항상 제외)
  window:<ref|uuid>   그 창의 워크스페이스 전부   예) window:15
  group:<이름>         그 그룹의 멤버 전부          예) group:투자 금융
  ws:<제목일부>        제목이 포함하는 워크스페이스  예) ws:TOPIK
"""
import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

# ── 대시보드 모듈 재사용 (전부 stdlib 의존이라 venv 불필요) ────────────────────────────
# 이 파일이 저장소 안(`tools/`)에 있으면 부모가 곧 대시보드다. 밖에 복사해 쓰는 경우를 위해
# 환경변수와 흔한 위치도 함께 본다.
DASH_CANDIDATES = [
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.environ.get("CMUX_DASHBOARD_DIR"),
    os.path.expanduser("~/cmux-state-dashboard"),
]


def _load_dashboard():
    """대시보드 폴더를 찾아 sys.path 에 넣고 모듈을 반환. 못 찾으면 명확히 실패."""
    for cand in DASH_CANDIDATES:
        if not cand:
            continue
        cand = os.path.expanduser(cand)
        if os.path.isfile(os.path.join(cand, "restore.py")):
            sys.path.insert(0, cand)
            import claude_index, cmux_client, config, layout, restore  # noqa: E401
            return cand, layout, restore, cmux_client, claude_index, config
    sys.exit(
        "대시보드 모듈을 찾지 못했습니다(복원 엔진). 폴더를 옮겼다면 환경변수로 알려주세요:\n"
        "  export CMUX_DASHBOARD_DIR=/path/to/cmux-state-dashboard"
    )


DASH, layout_mod, restore_mod, cmux_client, claude_index, dash_config = _load_dashboard()

SAVE_DIR = os.path.join(dash_config.DATA_DIR, "saved-layouts")
CMUX_BIN = dash_config.CMUX_BIN
MY_WS = os.environ.get("CMUX_WORKSPACE_ID", "").strip()   # 현재 Claude Code 세션 워크스페이스


# ── 공통 유틸 ────────────────────────────────────────────────────────────────────
def _now():
    return _dt.datetime.now().astimezone()


def _slug(text, maxlen=28):
    """파일명용 슬러그. 한글 보존(NFC), 경로/공백 문자만 정리."""
    text = unicodedata.normalize("NFC", (text or "").strip())
    text = re.sub(r"[\s/\\:*?\"<>|]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    return text[:maxlen] or "auto"


def _tree():
    try:
        return cmux_client.system_tree()
    except cmux_client.CmuxError as e:
        sys.exit(f"cmux 소켓 조회 실패: {e}")


def _read_native():
    with open(dash_config.NATIVE_JSON, encoding="utf-8") as f:
        return json.load(f)


def _capture(wait_fresh=10):
    """현재 상태 → layoutWindows.

    ⚠️ 네이티브 세션 JSON은 **디바운스 저장**이라 방금 만든/닫은 워크스페이스가 아직 없을 수 있다
    (소켓 tree = 라이브, native = 디스크). 복원에 필요한 스플릿 비율·resume 명령·URL은 native 에만
    있으므로 native 를 원천으로 쓰되, tree 에만 있는 워크스페이스가 사라질 때까지 잠깐 기다린다.
    """
    tree = _tree()
    live_ids = {ws.get("id") for w in tree.get("windows", []) for ws in w.get("workspaces", [])
                if ws.get("id")}
    deadline = time.time() + max(0, wait_fresh)
    native, missing = None, set()
    while True:
        native = _read_native()
        lw = layout_mod.parse_native_layout(native, claude_index.session_meta)
        native_ids = {ws.get("workspaceId") for w in lw for ws in w["workspaces"]}
        missing = {i for i in live_ids if i and i not in native_ids}
        if not missing or time.time() >= deadline:
            break
        time.sleep(1.0)
    if missing:
        print(f"⚠️ 네이티브 저장 지연: 라이브에만 있는 워크스페이스 {len(missing)}개는 스냅샷에서 빠집니다"
              f"(cmux가 디스크에 반영하기 전). 잠시 후 다시 저장하면 포함됩니다.", file=sys.stderr)
    return lw


def _stats(lw):
    """스냅샷 통계.

    분류는 대시보드 정식 분류기 `layout.panel_kind`(claude=resume 있는 터미널)를 그대로 쓴다.
    단 그룹 anchor("Group N" 자동 헤더 워크스페이스)는 **제외** — 사용자 콘텐츠가 아니라
    group.create 가 재생성하는 것이라, 사용자에게 보여줄 "워크스페이스 개수"에서 빼는 게 정확하다
    (그래서 dashboard 의 layout_stats 보다 워크스페이스·터미널 수가 anchor 수만큼 적다).
    """
    win = len(lw)
    groups = sum(len(w.get("groups", [])) for w in lw)
    ws = cl = term = br = 0
    for w in lw:
        for x in w["workspaces"]:
            if x.get("isGroupAnchor"):
                continue
            ws += 1
            for p in x["panels"].values():
                k = layout_mod.panel_kind(p)
                if k == "browser":
                    br += 1
                elif k == "claude":
                    cl += 1
                else:
                    term += 1
    return {"windows": win, "groups": groups, "workspaces": ws,
            "claude": cl, "terminals": term, "browsers": br}


def _summary_lines(lw, st, when):
    """사람이 읽는 자연어 요약(여러 줄)."""
    out = [
        f"{when:%Y-%m-%d %H:%M} 시점 cmux 상태 — "
        f"창 {st['windows']}개 · 그룹 {st['groups']}개 · 워크스페이스 {st['workspaces']}개 · "
        f"탭 {st['claude'] + st['terminals'] + st['browsers']}개"
        f"(claude {st['claude']} · 터미널 {st['terminals']} · 브라우저 {st['browsers']})"
    ]
    for w in lw:
        gname = {g["id"]: g.get("name") or "(이름없음)" for g in w.get("groups", [])}
        members, loose = {}, []
        for x in w["workspaces"]:
            if x.get("isGroupAnchor"):
                continue
            gid = x.get("groupId")
            if gid in gname:
                members.setdefault(gid, []).append(x)
            else:
                loose.append(x)
        out.append(f"· 창 {w['index'] + 1}: 워크스페이스 "
                   f"{sum(1 for x in w['workspaces'] if not x.get('isGroupAnchor'))}개")
        for gid, ms in members.items():
            titles = ", ".join((m.get("title") or "(무제)") for m in ms)
            out.append(f"   📁 {gname[gid]} ({len(ms)}): {titles}")
        for x in loose:
            kinds = [layout_mod.panel_kind(p) for p in x["panels"].values()]
            n_cl = kinds.count("claude")
            n_br = kinds.count("browser")
            bits = [b for b in (f"claude {n_cl}" if n_cl else "", f"브라우저 {n_br}" if n_br else "") if b]
            out.append(f"   · {x.get('title') or '(무제)'}" + (f" ({' · '.join(bits)})" if bits else ""))
    return out


def _md(doc):
    """저장 스냅샷 → 사람이 읽는 마크다운(에디터/Finder 에서 바로 파악용)."""
    st, lw = doc["stats"], doc["layoutWindows"]
    when = _dt.datetime.fromisoformat(doc["savedAt"])
    lines = [f"# cmux 레이아웃 스냅샷 — {doc.get('label') or '(라벨 없음)'}", "",
             f"- 저장 시각: {when:%Y-%m-%d %H:%M:%S (%a)}",
             f"- 규모: 창 {st['windows']} · 그룹 {st['groups']} · 워크스페이스 {st['workspaces']} · "
             f"claude {st['claude']} · 터미널 {st['terminals']} · 브라우저 {st['browsers']}"]
    if doc.get("note"):
        lines.append(f"- 메모: {doc['note']}")
    if doc.get("closedAfterSave"):
        lines.append(f"- 저장 후 닫음: {doc['closedAfterSave']}")
    lines += ["", "## 요약", ""] + [f"{s}" for s in doc.get("summary", [])] + ["", "## 상세", ""]
    for w in lw:
        gname = {g["id"]: g for g in w.get("groups", [])}
        lines.append(f"### 창 {w['index'] + 1}")
        for x in w["workspaces"]:
            if x.get("isGroupAnchor"):
                continue
            g = gname.get(x.get("groupId"))
            head = f"- **{x.get('title') or '(무제)'}**"
            if g:
                badge = "".join(["📌" if g.get("pinned") else "", "▸" if g.get("collapsed") else ""])
                head += f"  〔📁 {g.get('name')}{badge}〕"
            if x.get("pinned"):
                head += " 📌"
            lines.append(head + f"  `{x.get('cwd') or ''}`")
            for p in x["panels"].values():
                k = layout_mod.panel_kind(p)
                if k == "claude":
                    sid = (p.get("sessionId") or "")[:8]
                    lines.append(f"    - 🟠 claude `{sid}` {p.get('label') or p.get('title') or ''}")
                elif k == "browser":
                    lines.append(f"    - 🌐 {p.get('title') or ''} {p.get('url') or ''}")
                else:
                    lines.append(f"    - 🖥 터미널 {p.get('title') or ''}")
        lines.append("")
    lines += ["## 복원", "", "```bash",
              f"python3 ~/.claude/skills/cmux-config/scripts/cmux-snapshot.py restore {doc['id']}",
              "```", ""]
    return "\n".join(lines)


# ── 스냅샷 파일 I/O ──────────────────────────────────────────────────────────────
def _snapshot_files():
    if not os.path.isdir(SAVE_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(SAVE_DIR), reverse=True):
        if fn.endswith(".json"):
            out.append(os.path.join(SAVE_DIR, fn))
    return out


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve(ref):
    """id/번호/라벨 일부/latest → 스냅샷 파일 경로."""
    files = _snapshot_files()
    if not files:
        sys.exit("저장된 스냅샷이 없습니다. 먼저 `save` 하세요.")
    ref = (ref or "latest").strip()
    if ref in ("latest", "last", "최근"):
        return files[0]
    if ref.isdigit() and 1 <= int(ref) <= len(files):
        return files[int(ref) - 1]
    key = unicodedata.normalize("NFC", ref).lower()
    hits = [p for p in files
            if key in unicodedata.normalize("NFC", os.path.basename(p)).lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"'{ref}' 에 해당하는 스냅샷을 찾지 못했습니다. `list` 로 확인하세요.")
    sys.exit("여러 개가 일치합니다:\n  " + "\n  ".join(os.path.basename(p) for p in hits))


# ── 범위(scope) 해석 ─────────────────────────────────────────────────────────────
def _scope_workspaces(scope, lw, tree, within=None):
    """scope 문자열 → 닫을/복원할 워크스페이스 UUID 집합 (라이브 tree 기준).

    ★현재 Claude Code 세션 워크스페이스는 무조건 제외한다(자기 자신을 닫는 사고 방지).
    within: 창 ref/uuid 를 주면 그 창 안으로 **교집합 제한**한다(`--in window:24`).
        같은 제목의 워크스페이스가 여러 창에 있을 때(복원 사본 등) 원본까지 쓸어담는 사고를 막는다.
    """
    live = {}     # uuid -> (win_ref, title, pinned)
    for w in tree.get("windows", []):
        for ws in w.get("workspaces", []):
            if ws.get("id"):
                live[ws["id"]] = (w.get("ref"), ws.get("title") or "", bool(ws.get("pinned")))
    # native 기준 그룹 멤버십(그룹 이름 → 워크스페이스 UUID)
    gmembers = {}
    for w in lw:
        gname = {g["id"]: (g.get("name") or "") for g in w.get("groups", [])}
        for x in w["workspaces"]:
            gid = x.get("groupId")
            if gid in gname and x.get("workspaceId"):
                gmembers.setdefault(unicodedata.normalize("NFC", gname[gid]).lower(), set()).add(
                    x["workspaceId"])

    picked = set()
    for part in [p.strip() for p in (scope or "").split(",") if p.strip()]:
        low = unicodedata.normalize("NFC", part).lower()
        if low == "all":
            picked |= set(live)
        elif low.startswith("window:"):
            want = part.split(":", 1)[1].strip()
            for uid, (wref, _t, _p) in live.items():
                if wref == want or want in (wref or ""):
                    picked.add(uid)
            # UUID/인덱스로도 매칭
            for w in tree.get("windows", []):
                if want in (w.get("id") or "") or want == w.get("ref"):
                    picked |= {ws["id"] for ws in w.get("workspaces", []) if ws.get("id")}
        elif low.startswith("group:"):
            want = unicodedata.normalize("NFC", part.split(":", 1)[1].strip()).lower()
            for gname, ids in gmembers.items():
                if want and want in gname:
                    picked |= {i for i in ids if i in live}
        elif low.startswith("ws:"):
            want = unicodedata.normalize("NFC", part.split(":", 1)[1].strip()).lower()
            for uid, (_w, title, _p) in live.items():
                if want and want in unicodedata.normalize("NFC", title).lower():
                    picked.add(uid)
        else:
            sys.exit(f"알 수 없는 범위: {part!r} (all | window:X | group:이름 | ws:제목)")
    if within:                          # 창 교집합 제한(--in)
        keep = set()
        for w in tree.get("windows", []):
            if within == w.get("ref") or within in (w.get("id") or "") \
                    or within.replace("window:", "") == str(w.get("index", "")):
                keep |= {ws["id"] for ws in w.get("workspaces", []) if ws.get("id")}
        if not keep:
            sys.exit(f"--in {within} 에 해당하는 창을 찾지 못했습니다.")
        picked &= keep
    if MY_WS:
        picked.discard(MY_WS)          # ★자기 세션 보호
    else:
        print("⚠️ CMUX_WORKSPACE_ID 없음 — 현재 세션 워크스페이스를 식별할 수 없어 보호가 불완전합니다.",
              file=sys.stderr)
    return picked, live


def _close_workspaces(uuids, live, lw=None, yes=False):
    """워크스페이스 닫기. 항상 목록을 먼저 보여주고, --yes 없으면 실행하지 않는다.

    ⚠️ close-window 는 소켓에서 무효(OK만 반환) → **workspace close** 로 워크스페이스를 닫고,
       한 창의 워크스페이스가 전부 닫히면 빈 창은 자동 종료된다.
       pinned 워크스페이스는 "Pinned workspaces can't be closed" → unpin 선행 필수.

    ★안전장치(2026-07-24 사고 반영): 제목 필터(`ws:`)는 **같은 제목의 다른 창 워크스페이스까지**
      잡는다(복원 사본 vs 원본). 그래서 ①창별로 묶어 보여주고 ②여러 창에 걸치면 경고하고
      ③**닫으면 죽는 실행중 claude 세션 수**를 명시한다 — 닫기의 진짜 비용은 프로세스 종료다.
    """
    if not uuids:
        print("닫을 대상이 없습니다.")
        return 0
    # 워크스페이스 → 그 안의 claude sid (native 기준) → 실행중인지
    ws_sids = {}
    for w in (lw or []):
        for x in w["workspaces"]:
            wid = x.get("workspaceId")
            if wid:
                ws_sids[wid] = [p["sessionId"] for p in x["panels"].values() if p.get("sessionId")]
    running = cmux_client.running_claude_sids()
    by_win = {}
    for uid in uuids:
        by_win.setdefault(live.get(uid, ("?",))[0] or "?", []).append(uid)

    total_kill = 0
    print(f"\n닫을 대상 {len(uuids)}개 (창 {len(by_win)}개에 분포):")
    for wref in sorted(by_win):
        print(f"  [{wref}]")
        for uid in by_win[wref]:
            _w, title, pinned = live.get(uid, ("?", "(알수없음)", False))
            alive = [s for s in ws_sids.get(uid, []) if s and s.lower() in running]
            total_kill += len(alive)
            mark = f"  ⚠️ 실행중 claude {len(alive)}개 종료됨" if alive else ""
            print(f"    - {title or '(무제)'}{' 📌' if pinned else ''}  {uid[:8]}{mark}")
    if len(by_win) > 1:
        print("\n  ⚠️ 대상이 **여러 창**에 걸쳐 있습니다 — 같은 제목의 원본까지 닫힐 수 있습니다."
              "\n     한 창만 정리하려면 `--in window:N` 을 붙이세요.")
    if total_kill:
        print(f"\n  ⚠️ 이 작업으로 **실행중 claude {total_kill}개가 종료**됩니다"
              "(세션 기록은 디스크에 남아 나중에 복원 가능).")
    if MY_WS:
        print(f"  (제외됨: 현재 Claude Code 세션 워크스페이스 {MY_WS[:8]})")
    if not yes:
        print("\n※ 실제로 닫으려면 같은 명령에 --yes 를 붙이세요. (지금은 목록만 표시)")
        return 0
    closed = 0
    for _pass in range(3):                      # pinned/타이밍으로 남는 것 대비 반복
        remain = []
        for uid in uuids:
            try:
                subprocess.run([CMUX_BIN, "workspace-action", "--action", "unpin",
                                "--workspace", uid], capture_output=True, timeout=15)
                r = subprocess.run([CMUX_BIN, "workspace", "close", "--workspace", uid],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode == 0 and "Error" not in (r.stdout + r.stderr):
                    closed += 1
                else:
                    remain.append(uid)
            except (subprocess.SubprocessError, OSError):
                remain.append(uid)
        uuids = remain
        if not uuids:
            break
        time.sleep(1.0)
    print(f"\n✅ {closed}개 워크스페이스를 닫았습니다"
          + (f" (실패 {len(uuids)}개)" if uuids else " — 빈 창은 자동 종료됩니다."))
    return closed


# ── 서브커맨드 ──────────────────────────────────────────────────────────────────
def cmd_save(a):
    os.makedirs(SAVE_DIR, exist_ok=True)
    when = _now()
    lw = _capture()
    st = _stats(lw)
    if not st["workspaces"]:
        sys.exit("저장할 워크스페이스가 없습니다.")
    label = a.label or "auto"
    sid = (f"{when:%Y%m%d-%H%M}_{_slug(label)}"
           f"_w{st['windows']}g{st['groups']}ws{st['workspaces']}cl{st['claude']}")
    doc = {
        "schema": 1, "id": sid, "savedAt": when.isoformat(), "label": a.label or None,
        "note": a.note or None, "host": os.uname().nodename,
        "stats": st, "summary": _summary_lines(lw, st, when), "layoutWindows": lw,
    }
    jpath = os.path.join(SAVE_DIR, sid + ".json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    with open(os.path.join(SAVE_DIR, sid + ".md"), "w", encoding="utf-8") as f:
        f.write(_md(doc))
    print(f"✅ 저장: {jpath}")
    for s in doc["summary"]:
        print("   " + s)

    if a.close:
        picked, live = _scope_workspaces(a.close, lw, _tree(), within=a.__dict__.get("in_window"))
        n = _close_workspaces(picked, live, lw, yes=a.yes)
        if n:
            doc["closedAfterSave"] = a.close
            with open(jpath, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=1)
            with open(os.path.join(SAVE_DIR, sid + ".md"), "w", encoding="utf-8") as f:
                f.write(_md(doc))
    return 0


def cmd_list(a):
    files = _snapshot_files()[: a.limit]
    if not files:
        print("저장된 스냅샷이 없습니다.")
        return 0
    print(f"저장된 cmux 레이아웃 스냅샷 {len(files)}개 (최신순, 번호로 restore 가능)\n")
    for i, p in enumerate(files, 1):
        try:
            d = _load(p)
        except (OSError, json.JSONDecodeError):
            continue
        st = d.get("stats", {})
        when = _dt.datetime.fromisoformat(d["savedAt"])
        head = f"{i}. {when:%Y-%m-%d %H:%M}  {d.get('label') or '(라벨 없음)'}"
        print(head)
        print(f"     창 {st.get('windows')} · 그룹 {st.get('groups')} · WS {st.get('workspaces')} · "
              f"claude {st.get('claude')} · 터미널 {st.get('terminals')} · 브라우저 {st.get('browsers')}")
        if d.get("note"):
            print(f"     메모: {d['note']}")
        if d.get("closedAfterSave"):
            print(f"     (저장 후 닫음: {d['closedAfterSave']})")
        print(f"     id: {d.get('id')}")
    return 0


def cmd_show(a):
    d = _load(_resolve(a.ref))
    print(_md(d))
    return 0


def cmd_restore(a):
    path = _resolve(a.ref)
    d = _load(path)
    lw = d["layoutWindows"]
    selections = None
    if a.only:
        picked, _live = _scope_workspaces(a.only, lw, _tree())
        # 라이브 기준 scope 는 이미 닫힌 워크스페이스를 못 잡으므로, 스냅샷 제목/그룹으로도 매칭
        want_ids = set(picked)
        for part in [p.strip() for p in a.only.split(",") if p.strip()]:
            low = unicodedata.normalize("NFC", part).lower()
            for w in lw:
                gname = {g["id"]: unicodedata.normalize("NFC", g.get("name") or "").lower()
                         for g in w.get("groups", [])}
                for x in w["workspaces"]:
                    t = unicodedata.normalize("NFC", x.get("title") or "").lower()
                    if low.startswith("ws:") and low.split(":", 1)[1] in t:
                        want_ids.add(x["id"])
                    elif low.startswith("group:") and gname.get(x.get("groupId"), "") \
                            and low.split(":", 1)[1] in gname.get(x.get("groupId"), ""):
                        want_ids.add(x["id"])
        # want_ids 는 UUID(live) 또는 스냅샷 id 가 섞임 → 스냅샷 id 로 정규화
        sel_ids = set()
        for w in lw:
            for x in w["workspaces"]:
                if x["id"] in want_ids or x.get("workspaceId") in want_ids:
                    sel_ids.add(x["id"])
        if not sel_ids:
            sys.exit(f"'{a.only}' 에 해당하는 워크스페이스가 스냅샷에 없습니다.")
        selections = [{"wsId": i, "panelIds": None} for i in sel_ids]

    st = d.get("stats", {})
    # 실제 복원되는 것만 집계: 그룹 anchor("Group N")는 group.create 가 재생성하므로 제외된다.
    sel_set = {s["wsId"] for s in selections} if selections else None
    targets = [x for w in lw for x in w["workspaces"]
               if not x.get("isGroupAnchor") and (sel_set is None or x["id"] in sel_set)]
    n_cl = sum(1 for x in targets for p in x["panels"].values()
               if layout_mod.panel_kind(p) == "claude")
    print(f"복원 대상: {d.get('label') or d['id']} — 워크스페이스 {len(targets)}개 · claude {n_cl}개"
          f"{' (필터 ' + a.only + ')' if a.only else ''} → {'현재 창' if a.target == 'current' else '새 창'}")
    if a.dry_run:
        for w in lw:
            gname = {g["id"]: g.get("name") for g in w.get("groups", [])}
            for x in targets:
                if x not in w["workspaces"]:
                    continue
                g = gname.get(x.get("groupId"))
                kinds = [layout_mod.panel_kind(p) for p in x["panels"].values()]
                print(f"   · {x.get('title') or '(무제)'}"
                      + (f"  〔📁 {g}〕" if g else "")
                      + f"  claude {kinds.count('claude')} · 브라우저 {kinds.count('browser')}")
        print(f"\n※ 실제 복원하려면 --dry-run 을 빼세요"
              f"(claude 자동실행 상한 {restore_mod.MAX_CLAUDE_AUTORUN}).")
        return 0

    out = restore_mod.restore_layout_windows(
        lw, selections, target=a.target, autorun=not a.no_claude)
    print(f"✅ 복원 {out['restored']}/{out['workspaces']} 워크스페이스 · 그룹 {out['groups']}개"
          f" · claude 자동실행 {out['autorunInjected']}개"
          f"(상한 {restore_mod.MAX_CLAUDE_AUTORUN}, 이미 실행중인 세션은 제외)")
    fails = [r for r in out["results"] if not r.get("ok")]
    for r in fails:
        print(f"   ⚠️ 실패: {r.get('workspace')} — {r.get('error')}")
    # 안내는 '이번에 복원한 대상'의 claude 수(n_cl) 기준 — 스냅샷 전체 수를 쓰면 필터 복원 시 과장된다.
    if n_cl > out["autorunInjected"]:
        print(f"   ℹ️ claude {n_cl - out['autorunInjected']}개는 자동 실행되지 않았습니다"
              " (상한 초과이거나 이미 실행중). 이미 실행중이면 그대로 쓰시고, 아니면 해당 터미널에서"
              " 수동 resume 하세요.")
    return 0


def cmd_close(a):
    lw = _capture(wait_fresh=0)      # 그룹 이름·claude sid 해석용(닫기 대상은 라이브 tree 기준)
    picked, live = _scope_workspaces(a.scope, lw, _tree(), within=a.__dict__.get("in_window"))
    _close_workspaces(picked, live, lw, yes=a.yes)
    return 0


def cmd_delete(a):
    path = _resolve(a.ref)
    trash = os.path.expanduser("~/.Trash")
    for p in (path, path[:-5] + ".md"):
        if os.path.exists(p):
            dst = os.path.join(trash, os.path.basename(p))
            i = 1
            while os.path.exists(dst):
                dst = os.path.join(trash, f"{os.path.basename(p)}.{i}")
                i += 1
            os.rename(p, dst)      # 직접 삭제 금지 — 휴지통으로 이동
            print(f"휴지통으로 이동: {dst}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="cmux 레이아웃 임시 저장·복원·정리")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("save", help="현재 상태 저장(+선택적으로 원본 닫기)")
    p.add_argument("--label", help="스냅샷 이름(파일명에 들어감)")
    p.add_argument("--note", help="메모(자유 텍스트)")
    p.add_argument("--close", help="저장 후 닫을 범위: all | window:X | group:이름 | ws:제목")
    p.add_argument("--in", dest="in_window", metavar="window:N",
                   help="닫기 범위를 이 창 안으로 제한(같은 제목의 다른 창 원본 보호)")
    p.add_argument("--yes", action="store_true", help="닫기를 실제로 실행(없으면 목록만 표시)")
    p.set_defaults(fn=cmd_save)

    p = sub.add_parser("list", help="저장된 스냅샷 목록")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="스냅샷 상세(마크다운)")
    p.add_argument("ref", nargs="?", default="latest")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("restore", help="스냅샷 복원")
    p.add_argument("ref", nargs="?", default="latest")
    p.add_argument("--target", choices=["new", "current"], default="new")
    p.add_argument("--only", help="일부만 복원: group:이름 | ws:제목 | window:X")
    p.add_argument("--no-claude", action="store_true", help="claude 자동 실행 없이 구조만")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("close", help="라이브 워크스페이스 닫기(목록 확인 후 --yes)")
    p.add_argument("scope")
    p.add_argument("--in", dest="in_window", metavar="window:N",
                   help="이 창 안으로 제한(같은 제목의 다른 창 원본 보호)")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("delete", help="저장된 스냅샷을 휴지통으로")
    p.add_argument("ref")
    p.set_defaults(fn=cmd_delete)

    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()

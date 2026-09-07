"""네이티브 cmux 세션 JSON → 충실 복원용 레이아웃 트리 파서.

네이티브 세션 JSON(session-com.cmuxterm.app.json)은 100% 충실 복원에 필요한 모든 것을
담고 있다: 재귀 split 트리(dividerPosition/orientation), pane.panelIds/selectedPanelId(탭 그룹·순서·선택),
terminal.resumeBinding.command(claude resume), browser.urlString(URL), customTitle/Color/isPinned,
currentDirectory, window.frame.

소켓 tree 는 dividerPosition(분할 비율)을 주지 않으므로 레이아웃 원천은 항상 네이티브 JSON이다.
(snapshotter 는 이 결과를 normalized["layoutWindows"] 로 스냅샷에 보존한다.)
"""
import re

import claude_index

_UUID = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)


def _panel_info(p):
    """네이티브 panel → {id,type,title,cwd,url?,resumeCommand?,sessionId?,pinned}."""
    typ = p.get("type")
    info = {
        "id": p.get("id"),
        "type": typ,
        "title": p.get("title") or "",
        "pinned": bool(p.get("isPinned")),
    }
    if typ == "terminal":
        t = p.get("terminal") or {}
        info["cwd"] = t.get("workingDirectory") or p.get("directory")
        cmd = (t.get("resumeBinding") or {}).get("command")
        if cmd:
            info["resumeCommand"] = cmd
            m = _UUID.search(cmd)
            if m:
                info["sessionId"] = m.group(1).lower()
    elif typ == "browser":
        b = p.get("browser") or {}
        info["url"] = b.get("urlString") or None
    return info


def _sanitize_layout(node):
    """네이티브 layout 트리를 복원에 필요한 키만 남겨 복제(재귀).

    반환: {type:'pane', panelIds, selectedPanelId}
        | {type:'split', orientation, dividerPosition, first, second}
    """
    if not isinstance(node, dict):
        return None
    t = node.get("type")
    if t == "pane":
        pane = node.get("pane") or {}
        return {
            "type": "pane",
            "panelIds": list(pane.get("panelIds") or []),
            "selectedPanelId": pane.get("selectedPanelId"),
        }
    if t == "split":
        sp = node.get("split") or {}
        return {
            "type": "split",
            "orientation": sp.get("orientation"),  # 'horizontal'|'vertical'
            "dividerPosition": sp.get("dividerPosition"),
            "first": _sanitize_layout(sp.get("first")),
            "second": _sanitize_layout(sp.get("second")),
        }
    return None


def panel_kind(p):
    """칩/통계용 종류: claude(resume 있는 터미널) | terminal | browser."""
    if p.get("type") == "browser":
        return "browser"
    if p.get("sessionId") or p.get("resumeCommand"):
        return "claude"
    return "terminal"


def _parse_groups(wg):
    """tabManager.workspaceGroups → 정규화된 그룹 정의 리스트.

    cmux 0.64.17+ 워크스페이스 그룹: id·name·isCollapsed·isPinned·customColor·iconSymbol·
    anchorWorkspaceId. 워크스페이스는 각자 groupId 로 소속 그룹을 참조한다.
    """
    out = []
    for g in (wg or []):
        if not g.get("id"):
            continue
        out.append({
            "id": g.get("id"),
            "name": g.get("name") or "",
            "collapsed": bool(g.get("isCollapsed")),
            "pinned": bool(g.get("isPinned")),
            "color": g.get("customColor"),
            "icon": g.get("iconSymbol"),
            "anchorWorkspaceId": g.get("anchorWorkspaceId"),
        })
    return out


def parse_native_layout(native, meta_fn=None):
    """네이티브 세션 JSON → layoutWindows 리스트.

    meta_fn: sessionId → claude_index.session_meta 결과(label/lastActivity/exists 보강용).
    """
    out = []
    if not native:
        return out
    for wi, w in enumerate(native.get("windows", [])):
        tm = w.get("tabManager") or {}
        sel_ws = tm.get("selectedWorkspaceIndex")
        groups = _parse_groups(tm.get("workspaceGroups"))
        # 각 그룹의 anchor("Group N" 자동 헤더) workspaceId 집합 — 복원 시 제외/UI 숨김용
        anchor_ids = {g["anchorWorkspaceId"] for g in groups if g.get("anchorWorkspaceId")}
        wss = []
        for si, ws in enumerate(tm.get("workspaces", [])):
            panels = {}
            for p in ws.get("panels", []):
                pid = p.get("id")
                if not pid:
                    continue
                pinfo = _panel_info(p)
                sid = pinfo.get("sessionId")
                if meta_fn and sid:
                    m = meta_fn(sid) or {}
                    pinfo["exists"] = m.get("exists")
                    if m.get("exists"):
                        pinfo["label"] = m.get("label")
                        pinfo["lastActivity"] = m.get("lastActivity")
                        pinfo["msgCount"] = m.get("msgCount")
                        pinfo["projectDir"] = m.get("projectDir")
                panels[pid] = pinfo
            wsid = ws.get("workspaceId")
            wss.append({
                "wsIndex": si,
                "id": f"w{wi}:ws{si}",
                "workspaceId": wsid,                 # 네이티브 UUID(그룹 anchor 대조용)
                "title": ws.get("customTitle") or ws.get("processTitle") or "(무제)",
                "color": ws.get("customColor"),
                "pinned": bool(ws.get("isPinned")),
                "description": ws.get("customDescription") or "",
                "cwd": ws.get("currentDirectory"),
                "selected": (si == sel_ws),
                "groupId": ws.get("groupId"),        # 소속 워크스페이스 그룹(없으면 None)
                "isGroupAnchor": bool(wsid) and wsid in anchor_ids,  # "Group N" 자동 헤더=복원 제외
                "layout": _sanitize_layout(ws.get("layout")),
                "panels": panels,
            })
        out.append({
            "index": wi,
            "title": f"창 {wi + 1}",
            "frame": w.get("frame"),
            "selectedWorkspaceIndex": sel_ws,
            "groups": groups,   # 창의 워크스페이스 그룹 정의
            "workspaces": wss,
        })
    return out


def native_workspace_ids(layout_windows):
    """네이티브 레이아웃이 담고 있는 워크스페이스 UUID 집합(대소문자 무시).

    ⚠️ cmux 0.64.17(2026-07-21) 이전 세션 JSON 에는 workspaceId 가 아예 없다 → 빈 집합이 된다.
    그 경우 UUID 대조는 '불가'이지 '불일치'가 아니므로, 호출부는 반드시 제목 대조로 넘어가야 한다
    (안 그러면 네이티브가 이미 담고 있는 창을 tree-only 로 오판해 **중복 복원**된다).
    """
    out = set()
    for w in layout_windows or []:
        for ws in w.get("workspaces", []):
            wsid = ws.get("workspaceId")
            if wsid:
                out.add(str(wsid).lower())
    return out


def native_workspace_titles(layout_windows):
    """네이티브 레이아웃의 워크스페이스 제목 집합(UUID 없는 구 스냅샷의 대조 키)."""
    out = set()
    for w in layout_windows or []:
        for ws in w.get("workspaces", []):
            key = claude_index.normalize_title(ws.get("title"))
            if key:
                out.add(key)
    return out


def _synth_panels(ws_node, title_lookup, meta_fn):
    """tree 워크스페이스 노드 → (panels, pane별 panelId 목록, pane별 selectedPanelId).

    터미널 서피스는 제목(=aiTitle)으로 세션을 되찾아 resume 명령을 만든다.
    """
    panels, panes = {}, []
    for pn in ws_node.get("panes", []):
        ids, sel = [], None
        for sf in pn.get("surfaces", []):
            pid = sf.get("id") or sf.get("ref")
            if not pid:
                continue
            typ = "browser" if sf.get("type") == "browser" else "terminal"
            info = {
                "id": pid,
                "type": typ,
                "title": sf.get("title") or "",
                "pinned": False,
            }
            if typ == "browser":
                info["url"] = sf.get("url") or None
                if not info["url"]:
                    continue          # URL 없는 브라우저 탭은 복원할 것이 없다
            else:
                sid = title_lookup(sf.get("title")) if title_lookup else None
                if sid:
                    info["sessionId"] = sid
                    cmd, cwd = claude_index.build_resume(sid)
                    if cmd:
                        info["resumeCommand"] = cmd
                    if cwd:
                        info["cwd"] = cwd
                    if meta_fn:
                        m = meta_fn(sid) or {}
                        info["exists"] = m.get("exists")
                        if m.get("exists"):
                            info["label"] = m.get("label")
                            info["lastActivity"] = m.get("lastActivity")
                            info["msgCount"] = m.get("msgCount")
                            info["projectDir"] = m.get("projectDir")
                            if not info.get("cwd"):
                                info["cwd"] = m.get("cwd")
            panels[pid] = info
            ids.append(pid)
            if sf.get("selected") and sel is None:
                sel = pid
        if ids:
            panes.append((ids, sel or ids[0]))
    return panels, panes


def _panes_to_layout(panes):
    """pane 목록 → split 트리(근사).

    tree 는 분할 방향·비율을 주지 않으므로 좌우 50:50 으로 편다. 탭 구성(panelIds)과
    pane 개수는 실제 그대로이므로, 비율만 다르고 내용은 원본과 같게 복원된다.
    """
    nodes = [{"type": "pane", "panelIds": ids, "selectedPanelId": sel} for ids, sel in panes]
    if not nodes:
        return None
    node = nodes[-1]
    for prev in reversed(nodes[:-1]):
        node = {"type": "split", "orientation": "horizontal",
                "dividerPosition": 0.5, "first": prev, "second": node}
    return node


def synthesize_tree_windows(tree_windows, layout_windows, meta_fn=None, title_lookup=None):
    """소켓 tree 에만 존재하는 창 → 복원 가능한 근사 layoutWindow 로 합성.

    cmux 가 세션 JSON 에 기록하지 않은 창(2026-08-04 실사고: 하루치 작업이 통째로 소실)은
    네이티브 파서로는 보이지 않아 복구 탭에 아예 뜨지 않았다. tree 스냅샷에는 워크스페이스
    제목·pane 구성·브라우저 URL 이 남아 있으므로, 그것으로 복원 가능한 형태를 만든다.

    유실되는 것: 분할 비율/방향, 워크스페이스 색상, 그룹. → approx=True 로 표시한다.
    """
    if not tree_windows:
        return []
    if title_lookup is None:
        title_lookup = claude_index.find_by_title
    native_ids = native_workspace_ids(layout_windows)
    native_titles = native_workspace_titles(layout_windows)
    base = len(layout_windows or [])
    out = []
    for w in tree_windows:
        wss = w.get("workspaces") or []
        if not wss:
            continue
        # 네이티브에 하나라도 걸치면 네이티브 쪽이 정본 — 중복 복원 방지
        if native_ids and any(str(ws.get("id") or "").lower() in native_ids for ws in wss):
            continue
        # workspaceId 가 없는 구 스냅샷(cmux < 0.64.17)은 UUID 대조가 불가능하다.
        # 제목으로 대조해 네이티브가 이미 담고 있는 창을 걸러낸다 — 이 폴백이 없으면
        # 같은 창이 네이티브+합성으로 두 번 잡혀 복원 시 워크스페이스가 두 배로 생긴다.
        if not native_ids and native_titles:
            keys = {claude_index.normalize_title(ws.get("title")) for ws in wss}
            keys.discard("")
            if keys and len(keys & native_titles) * 2 >= len(keys):
                continue
        synth = []
        for si, ws in enumerate(wss):
            panels, panes = _synth_panels(ws, title_lookup, meta_fn)
            if not panels:
                continue
            cwd = next((p.get("cwd") for p in panels.values() if p.get("cwd")), None)
            synth.append({
                "wsIndex": si,
                "id": f"t{base + len(out)}:ws{si}",
                "workspaceId": ws.get("id"),
                "title": ws.get("title") or "(무제)",
                "color": None,                       # tree 미제공
                "pinned": bool(ws.get("pinned")),
                "description": ws.get("description") or "",
                "cwd": cwd,
                "selected": bool(ws.get("selected")),
                "groupId": None,                     # tree 미제공
                "isGroupAnchor": False,
                "layout": _panes_to_layout(panes),
                "panels": panels,
            })
        if not synth:
            continue
        out.append({
            "index": base + len(out),
            "title": w.get("title") or f"창 {base + len(out) + 1}",
            "frame": None,
            "selectedWorkspaceIndex": None,
            "groups": [],
            "source": "tree",     # ← 복원 원천이 네이티브가 아님
            "approx": True,       # ← 분할 비율·색상·그룹 미복원
            "workspaces": synth,
        })
    return out


def layout_stats(layout_windows):
    """전체 claude/terminal/browser/워크스페이스 카운트."""
    n_c = n_t = n_b = n_ws = 0
    for w in layout_windows:
        for ws in w["workspaces"]:
            n_ws += 1
            for p in ws["panels"].values():
                k = panel_kind(p)
                if k == "browser":
                    n_b += 1
                elif k == "claude":
                    n_c += 1
                else:
                    n_t += 1
    return {"windows": len(layout_windows), "workspaces": n_ws,
            "claude": n_c, "terminal": n_t, "browser": n_b}


def layout_hash_parts(layout_windows):
    """레이아웃 구조 해시용 파츠.

    orientation/panelIds(탭 구성)/제목/색만 반영. selectedPanelId(탭 전환)·
    dividerPosition(리사이즈)은 제외 — 너무 잦은 스냅샷 생성을 막는다.
    """
    parts = ["schemaV2:groups"]   # 스키마 변경 시 bump → 새 스냅샷 강제

    def walk(node, prefix):
        if not isinstance(node, dict):
            return
        if node["type"] == "pane":
            parts.append(f"{prefix}P:{','.join(node['panelIds'])}")
        else:
            parts.append(f"{prefix}S:{node.get('orientation')}")
            walk(node.get("first"), prefix + "1")
            walk(node.get("second"), prefix + "2")

    for w in layout_windows:
        for g in w.get("groups", []):
            parts.append(f"G:{g['id']}:{g['name']}:{g['pinned']}:{g['collapsed']}")
        for ws in w["workspaces"]:
            parts.append(
                f"W{w['index']}:WS{ws['wsIndex']}:{ws['title']}:{ws.get('color')}:{ws.get('groupId')}")
            walk(ws.get("layout"), "")
    return parts

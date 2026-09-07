"""데모 모드 — cmux 없이도 관제실을 그대로 띄운다.

    CMUX_DASH_DEMO=1 python -m uvicorn app:app --port 7788
    또는  python run_demo.py

**UI 코드는 한 줄도 갈라지지 않는다.** `demo_fixture.py` 의 합성 상태를 실제 API 와
똑같은 모양으로 빚어서 내려 줄 뿐이라, 화면에 그려지는 건 진짜 `nav.js`/`board.css` 다.
그래서 여기서 찍은 스크린샷은 실물과 같고, 동시에 사용자의 실제 작업 제목은 한 글자도
들어 있지 않다.

## 세 가지 원칙
1. **시각은 요청 때 환산한다.** fixture 는 `ago`(초)만 갖고 있고 여기서 `now - ago` 로
   바꾼다. 그래서 언제 열어도 "8초 전 · 3분 전"이 살아 있다.
2. **제어도 진짜로 동작한다.** 포커스·고정·그룹 이동·배치 변경이 인메모리 상태를 실제로
   바꾼다. 데모에서 끌어다 놓으면 다음 폴링에 그 자리에 가 있다 — GIF 를 찍을 수 있어야
   해서다. 읽기만 되는 데모는 이 도구가 무엇인지 못 보여준다.
3. **판정은 서버가 한다.** 상태 순서·'막힘'의 정의는 데모에서도 `nav.STATUS_ORDER` 를
   그대로 쓴다. 데모가 자기만의 상태 집합을 적으면 정본과 소리 없이 어긋난다.
"""
from __future__ import annotations

import hashlib
import os
import random
import time
import uuid

import demo_fixture as fx

ENABLED = os.environ.get("CMUX_DASH_DEMO", "").strip() in ("1", "true", "yes", "on")

_NS = uuid.UUID("6f1d2c34-8b7a-4e56-9c0d-1a2b3c4d5e6f")   # 고정 네임스페이스


def uid(*parts) -> str:
    """이름에서 결정론적 UUID. 다시 띄워도 같은 id 라 스크린샷이 재현된다."""
    return str(uuid.uuid5(_NS, "|".join(str(p) for p in parts))).upper()


# ── 상태 → 표시 재료 ──────────────────────────────────────────────────────
STATUS_SOURCE = {
    "running":    "탭 제목 스피너",
    "permission": "알림(권한)",
    "question":   "알림(질문)",
    "background": "백그라운드 셸",
    "waiting":    "알림(입력 대기)",
    "idle":       "무활동",
}
NOTIF_KIND = {"permission": "permission", "question": "waiting",
              "waiting": "waiting", "background": "completed"}


# ── 상태 조립 ─────────────────────────────────────────────────────────────
def _build():
    """fixture → 내부 표현. 프로세스당 한 번만 만들고 이후엔 제어 API 가 변형한다."""
    groups = {}
    for gi, (key, name, icon, pinned) in enumerate(fx.GROUPS):
        groups[key] = {"id": uid("group", key), "name": name, "color": None,
                       "icon": icon, "pinned": pinned, "order": gi}

    windows, tabs = [], []
    ref = {"window": 0, "workspace": 0, "pane": 0, "surface": 0}

    def nxt(kind):
        ref[kind] += 1
        return f"{kind}:{ref[kind]}" if kind != "surface" else f"surface:{ref[kind]}"

    for wi, (label, human, frame) in enumerate(fx.WINDOWS):
        wref = nxt("window")
        win = {"id": uid("window", label), "ref": wref, "index": wi, "label": label,
               "name": human, "frame": frame, "workspaces": [], "visible": True}
        wsi = 0
        for gkey, rows in fx.LAYOUT.get(label, []):
            for (ws_title, tab_title, status, ago, prompt, cwd_leaf, extra) in rows:
                wsref, pref, sref = nxt("workspace"), nxt("pane"), nxt("surface")
                sid = uid("session", label, ws_title, tab_title)
                ws = {
                    "id": uid("ws", label, ws_title, wsi), "ref": wsref, "index": wsi,
                    "title": ws_title, "description": extra.get("desc"),
                    "color": extra.get("color"), "pinned": bool(extra.get("pinned")),
                    "groupKey": gkey, "selected": wsi == 0,
                    "paneRef": pref, "surfaceRef": sref,
                    "tabTitle": tab_title, "status": status, "ago": ago,
                    "prompt": prompt, "cwd": f"~/dev/{cwd_leaf}" if cwd_leaf != "~" else "~",
                    "sessionId": sid, "live": True, "extra": extra,
                    "browser": list(extra.get("browser") or []),
                    # 쪼갠 페인들. 첫 페인(claude 터미널)은 위에서 만든 paneRef 다.
                    "extraPanes": [
                        {"ref": nxt("pane"), "surfaceRef": nxt("surface"),
                         "kind": k, "title": t, "url": u}
                        for (k, t, u) in (extra.get("split") or [])
                    ],
                    "ratio": extra.get("ratio", 0.6),
                    "vertical": bool(extra.get("vertical")),
                }
                win["workspaces"].append(ws)
                tabs.append((win, ws))
                wsi += 1
        # 닫지 않고 남겨 둔 것들 — 화면 대부분을 채우는 게 이쪽이다
        for row in fx.EXTRA:
            if row[0] != label:
                continue
            _w, gkey, ws_title, tab_title, status, ago, prompt, cwd_leaf = row
            wsref, pref, sref = nxt("workspace"), nxt("pane"), nxt("surface")
            win["workspaces"].append({
                "id": uid("ws", label, ws_title, wsi), "ref": wsref, "index": wsi,
                "title": ws_title, "description": None, "color": None, "pinned": False,
                "groupKey": gkey, "selected": False, "paneRef": pref, "surfaceRef": sref,
                "tabTitle": tab_title, "status": status, "ago": ago, "prompt": prompt,
                "cwd": f"~/dev/{cwd_leaf}" if cwd_leaf != "~" else "~",
                "sessionId": uid("session", label, ws_title, tab_title),
                "live": True, "extra": {}, "browser": [],
            })
            wsi += 1
        # 살아 있는 claude 가 없는 워크스페이스 (라이브 트리에만 보인다)
        for (gkey, title, kind, url) in fx.IDLE_WORKSPACES.get(label, []):
            wsref, pref, sref = nxt("workspace"), nxt("pane"), nxt("surface")
            win["workspaces"].append({
                "id": uid("ws", label, title, wsi), "ref": wsref, "index": wsi,
                "title": title, "description": None, "color": None, "pinned": False,
                "groupKey": gkey, "selected": False, "paneRef": pref, "surfaceRef": sref,
                "tabTitle": title, "status": None, "ago": None, "prompt": None,
                "cwd": "~", "sessionId": None, "live": False, "extra": {},
                "kind": kind, "url": url, "browser": [],
            })
            wsi += 1
        # cmux 사이드바에서 한 그룹은 **연속한 한 덩어리**다(그래서 멤버십이 위치로 정해진다).
        # 손으로 쓴 것 + 남겨 둔 것 + claude 없는 것을 순서대로 붙이면 같은 그룹이 여러 번
        # 끊겨 나타나 실물과 다른 그림이 된다 → 처음 나온 순서를 지키며 그룹끼리 모은다.
        seen = []
        for ws in win["workspaces"]:
            if ws["groupKey"] not in seen:
                seen.append(ws["groupKey"])
        win["workspaces"].sort(key=lambda w: seen.index(w["groupKey"]))
        for i, ws in enumerate(win["workspaces"]):
            ws["index"] = i
            ws["selected"] = i == 0
        windows.append(win)

    return {"groups": groups, "windows": windows, "born": time.time()}


_STATE = None


def state():
    global _STATE
    if _STATE is None:
        _STATE = _build()
    return _STATE


def reset():
    """제어 API 로 흐트러진 배치를 원래대로. (`POST /api/demo/reset`)"""
    global _STATE
    _STATE = None
    return state()


# ── /api/nav ──────────────────────────────────────────────────────────────
def nav_payload(status_order, status_label, blocked):
    """'지금 어디' 응답. 정렬·상태 정의는 nav.py 가 준 정본을 그대로 쓴다."""
    import nav                                     # 순환 import 회피용 지연 import
    st, now = state(), time.time()
    rng = random.Random(20260907)                  # 흔들림도 재현 가능하게
    out = []

    # '지금 보는 탭' = 활성 창이 **지금 띄우고 있는** 워크스페이스. 사이드바 첫 칸이 아니다
    # (클릭으로 탭을 옮기면 selected 가 움직이는데 첫 칸은 그대로라 엉뚱한 카드에 배지가 붙는다).
    active_win = st["windows"][0]
    active_ws = next((w for w in active_win["workspaces"] if w["live"] and w["selected"]),
                     None) or next((w for w in active_win["workspaces"] if w["live"]), None)

    for win in st["windows"]:
        for ws in win["workspaces"]:
            if not ws["live"]:
                continue
            e, status = ws["extra"], ws["status"]
            last = now - ws["ago"]
            prompt_at = last - rng.randint(20, 900)
            is_active = ws is active_ws

            notif = None
            if status in NOTIF_KIND:
                notif = {"kind": NOTIF_KIND[status],
                         "title": ws["tabTitle"], "subtitle": win["label"],
                         "body": (e.get("perm") or (e.get("ask") or "").split("\n")[0]
                                  or "입력을 기다리는 중"),
                         "at": last, "unread": status in blocked}

            ws_tabs = [{"ref": ws["surfaceRef"], "type": "terminal",
                        "title": ws["tabTitle"], "url": None, "selected": True}]
            for bi, (btitle, burl) in enumerate(ws["browser"]):
                ws_tabs.append({"ref": f"{ws['surfaceRef']}b{bi}", "type": "browser",
                                "title": btitle, "url": burl, "selected": False})

            g = st["groups"][ws["groupKey"]]
            out.append({
                "pid": 40000 + (int(hashlib.md5(ws["id"].encode()).hexdigest(), 16) % 20000),
                "bgShells": int(e.get("bg") or 0),
                "sessionId": ws["sessionId"], "sessionIdSource": "명령줄",
                "lastHumanAt": prompt_at,
                "wokenBySystem": bool(e.get("bgDone")),
                "wakeKind": "백그라운드 완료" if e.get("bgDone") else None,
                "surfaceId": uid("surface", ws["id"]), "located": True,
                "isActiveTab": is_active,
                "title": ws["tabTitle"], "cleanTitle": nav.clean_title(ws["tabTitle"]),
                "status": status, "statusLabel": status_label[status],
                "statusSource": STATUS_SOURCE.get(status, "판정 불가"),
                "lastActivity": last, "activitySource": "대화",
                "lastMessageAt": last, "lastEntryAt": last + 0.3, "fileMtime": now - 1,
                "lastPromptAt": prompt_at, "lastPromptText": ws["prompt"],
                "lastRole": "user" if status in ("running", "background") else "assistant",
                "lastStop": None, "titleRenamedByUser": bool(e.get("renamed")),
                "askText": e.get("ask"),
                "cwd": ws["cwd"], "transcript": f"~/.claude/projects/demo/{ws['sessionId']}.jsonl",
                "notif": notif,
                "group": {k: g[k] for k in ("id", "name", "color", "icon", "pinned")},
                "workspaceColor": ws["color"], "wsTabs": ws_tabs,
                "paneIndex": 0, "surfaceIndex": 0,
                "windowId": win["id"], "windowRef": win["ref"], "windowIndex": win["index"],
                "windowLabel": win["label"], "windowVisible": win["visible"],
                "windowShowing": next((w["tabTitle"] for w in win["workspaces"]
                                       if w["selected"]), win["name"]),
                "workspaceId": ws["id"], "workspaceRef": ws["ref"],
                "workspaceTitle": ws["title"], "workspaceDescription": ws["description"],
                "workspaceIndex": ws["index"], "workspaceSelected": ws["selected"],
                "workspacePinned": ws["pinned"],
                "paneRef": ws["paneRef"], "surfaceRef": ws["surfaceRef"],
                "tabRef": ws["surfaceRef"].replace("surface:", "tab:"),
                "tabTitle": ws["tabTitle"], "selectedInPane": True, "paneCount": 1,
                "bgDoneAgo": (now - last) if e.get("bgDone") else None,
                "bgJustDone": bool(e.get("bgDone")),
            })

    out.sort(key=lambda t: (status_order.index(t["status"]) if t["status"] in status_order
                            else 9, -(t["lastActivity"] or 0)))
    # ⚠️ 상태별 그대로 센다 — nav.collect 과 **같은 모양**이어야 한다.
    # 처음엔 running/waiting/idle 셋으로 합쳐 내려보냈다가 헤더의 '막힘'이 계속 0 으로
    # 떴다. 화면은 `counts.permission + counts.question` 을 읽는데 그 키가 아예 없었던 것.
    # 데모가 자기만의 집합을 적으면 이렇게 조용히 어긋난다.
    counts = {}
    for t in out:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return {
        "generatedAt": now, "tabs": out, "counts": counts, "total": len(out),
        "activeSurfaceId": uid("surface", active_ws["id"]) if active_ws else None,
        "activeWindowId": active_win["id"], "activeWindowRef": active_win["ref"],
        "warnings": [], "treeStaleSec": 0,
        "statusOrder": status_order, "blockedStatuses": blocked,
        "statusLabel": status_label, "demo": True,
    }


# ── /api/state ────────────────────────────────────────────────────────────
def state_payload():
    """'현재' 탭 — 창 > 워크스페이스 > 탭 라이브 트리 + 세션 목록."""
    st, now = state(), time.time()
    windows, sessions, layout_windows = [], [], []

    for win in st["windows"]:
        wss, lay_ws = [], []
        for ws in win["workspaces"]:
            g = st["groups"][ws["groupKey"]]
            panes = [{"ref": ws["paneRef"], "surfaces": [
                {"ref": ws["surfaceRef"],
                 "type": "terminal" if ws["live"] else ws.get("kind", "terminal"),
                 "title": ws["tabTitle"],
                 "url": ws.get("url"),
                 "selected": True}]}]
            for bi, (btitle, burl) in enumerate(ws["browser"]):
                panes[0]["surfaces"].append(
                    {"ref": f"{ws['surfaceRef']}b{bi}", "type": "browser",
                     "title": btitle, "url": burl, "selected": False})
            for ep in ws.get("extraPanes", []):
                panes.append({"ref": ep["ref"], "surfaces": [
                    {"ref": ep["surfaceRef"], "type": ep["kind"],
                     "title": ep["title"], "url": ep["url"], "selected": True}]})

            claude = None
            if ws["live"]:
                claude = {
                    "sessionId": ws["sessionId"], "label": ws["tabTitle"],
                    "cwd": ws["cwd"],
                    "resumeCommand": _resume(ws), "plainResume": f"claude --resume {ws['sessionId']}",
                    "lastActivity": now - ws["ago"],
                    "msgCount": 40 + (int(ws["id"][:4], 16) % 900),
                    "exists": True, "projectDir": "demo", "running": True,
                }
                sessions.append(dict(claude, nativeTitle=ws["title"],
                                     windowIndex=win["index"]))
            wss.append({"id": ws["id"], "ref": ws["ref"], "title": ws["title"],
                        "selected": ws["selected"], "pinned": ws["pinned"],
                        "description": ws["description"], "color": ws["color"],
                        "groupId": g["id"], "claude": claude, "panes": panes})
            lay_ws.append({
                "wsIndex": ws["index"], "id": ws["id"], "workspaceId": ws["id"],
                "title": ws["title"], "color": ws["color"], "pinned": ws["pinned"],
                "description": ws["description"] or "", "cwd": ws["cwd"],
                "selected": ws["selected"], "groupId": g["id"], "isGroupAnchor": False,
                "layout": _layout_tree(ws),
                "panels": _panels(ws),
            })

        seen, gs = set(), []
        for ws in win["workspaces"]:
            if ws["groupKey"] in seen:
                continue
            seen.add(ws["groupKey"])
            g = st["groups"][ws["groupKey"]]
            gs.append({"id": g["id"], "name": g["name"], "collapsed": False,
                       "pinned": g["pinned"], "color": None, "icon": g["icon"],
                       "anchorWorkspaceId": win["workspaces"][0]["id"]})

        windows.append({"id": win["id"], "ref": win["ref"], "index": win["index"],
                        "title": win["name"],
                        "selectedWorkspaceId": next((w["id"] for w in win["workspaces"]
                                                     if w["selected"]), None),
                        "workspaces": wss})
        layout_windows.append({"index": win["index"], "title": win["name"],
                               "frame": win["frame"], "selectedWorkspaceIndex": 0,
                               "groups": gs, "workspaces": lay_ws})

    # 서피스를 실제로 센다. 파생값을 손으로 빼면(예: 터미널 = 전체 - 브라우저) 분할·탭이
    # 늘어난 순간 조용히 어긋난다 — 이 저장소가 8/4 에 크게 데인 자리가 정확히 그 종류다.
    n_ws = sum(len(w["workspaces"]) for w in windows)
    n_term = n_browser = 0
    for w in windows:
        for ws in w["workspaces"]:
            for pn in ws["panes"]:
                for sf in pn["surfaces"]:
                    if sf["type"] == "browser":
                        n_browser += 1
                    else:
                        n_term += 1
    stats = {"windows": len(windows), "workspaces": n_ws, "sessions": len(sessions)}
    lay = {"windows": len(windows), "workspaces": n_ws, "claude": len(sessions),
           "terminal": n_term, "browser": n_browser}
    return {
        "capturedAt": now, "windows": windows, "sessions": sessions, "source": "demo",
        "runningCount": len(sessions), "stats": stats,
        "layoutWindows": layout_windows, "layoutStats": lay,
        "treeOnlyStats": {k: 0 for k in lay},
        "sourceCheck": {"treeWindows": len(windows), "nativeWindows": len(windows),
                        "treeOnlyWindows": 0, "treeOnlyWorkspaces": 0,
                        "treeOnlyClaude": 0, "mismatch": False},
        "restorableStats": {"windows": len(windows), "workspaces": n_ws,
                            "claude": len(sessions)},
        "demo": True,
    }


def _layout_tree(ws):
    """페인 구성을 cmux 네이티브와 같은 재귀 split 트리로.

    `{type:'pane', panelIds, selectedPanelId}` 이거나
    `{type:'split', orientation, dividerPosition, first, second}` — 복구 탭 미니맵이
    이 트리를 그대로 중첩 flexbox 로 그린다.
    """
    def pane(ref):
        return {"type": "pane", "panelIds": [ref], "selectedPanelId": ref}

    node = pane(ws["paneRef"])
    extras = ws.get("extraPanes") or []
    orient = "vertical" if ws.get("vertical") else "horizontal"
    for i, ep in enumerate(extras):
        node = {"type": "split", "orientation": orient,
                # 두 번째 분할부터는 더 잘게 — 실제로 쪼개는 모양이 그렇다
                "dividerPosition": ws.get("ratio", 0.6) if i == 0 else 0.5,
                "first": node, "second": pane(ep["ref"])}
    return node


def _panels(ws):
    out = {ws["paneRef"]: {
        "type": "terminal" if ws["live"] else ws.get("kind", "terminal"),
        "title": ws["tabTitle"],
        "sessionId": ws["sessionId"],
        "resumeCommand": _resume(ws) if ws["live"] else None,
        "url": (ws["browser"][0][1] if ws["browser"] else ws.get("url")),
        "cwd": ws["cwd"]}}
    for ep in ws.get("extraPanes", []):
        out[ep["ref"]] = {"type": ep["kind"], "title": ep["title"],
                          "sessionId": None, "resumeCommand": None,
                          "url": ep["url"], "cwd": ws["cwd"]}
    return out


def _resume(ws):
    return ('"$([ -x "${CMUX_CLAUDE_WRAPPER_SHIM:-}" ] && printf %s '
            '"$CMUX_CLAUDE_WRAPPER_SHIM" || printf claude)" '
            f'--resume {ws["sessionId"]} --dangerously-skip-permissions')


# ── /api/health · /api/sessions/health ────────────────────────────────────
def health_payload(asset_version):
    st = state()
    n = sum(1 for w in st["windows"] for ws in w["workspaces"] if ws["live"])
    return {"ok": True, "socket": True, "controlAvailable": True, "selfHealed": False,
            "latestSnapshotId": SNAP_TOP, "latestSnapshotAt": time.time() - 12,
            "latestSessions": n, "assetVersion": asset_version, "demo": True}


def sessions_health_payload(hours):
    """세션 건강도 — `session_health.collect()` 와 **같은 모양**.

    ⚠️ 여기 필드 이름을 대충 지으면 화면이 조용히 빈칸을 그린다. 실제로 처음엔
    `idleSec`·`stale` 같은 자작 필드로 내려보냈다가 목록이 통째로 안 나왔다.
    """
    st, thr = state(), float(hours or 24)
    rows = []
    for win in st["windows"]:
        for ws in win["workspaces"]:
            if not ws["live"]:
                continue
            idle_h = ws["ago"] / 3600.0
            verdict = ("reap" if idle_h >= thr
                       else "candidate" if idle_h >= thr / 2 else "active")
            rows.append({
                "pid": 40000 + (int(ws["id"][:4], 16) % 20000),
                "tty": f"/dev/ttys{(ws['index'] * 7) % 200:03d}",
                "ttyIdleHours": round(idle_h, 1),
                "sessionIdleHours": round(idle_h, 1),
                "rssMb": 180 + (int(ws["id"][4:8], 16) % 620),
                "verdict": verdict, "sessionId": ws["sessionId"],
                "resumeRegistered": bool(ws["pinned"] or ws["color"]),
                "preview": ws["prompt"] or "",
                "etime": f"{int(idle_h):02d}:00:00",
            })
    rows.sort(key=lambda r: -r["ttyIdleHours"])

    def gb(v):
        return round(sum(r["rssMb"] for r in rows if r["verdict"] == v) / 1024, 1)

    def n(v):
        return sum(1 for r in rows if r["verdict"] == v)

    return {
        "available": True, "thresholdHours": thr, "candidateHours": thr / 2,
        "sessions": rows,
        "summary": {"total": len(rows),
                    "totalGb": round(sum(r["rssMb"] for r in rows) / 1024, 1),
                    "reap": n("reap"), "reapGb": gb("reap"),
                    "candidate": n("candidate"), "candidateGb": gb("candidate"),
                    "active": n("active")},
        "reapCommand": f"python3 ~/.claude/scripts/claude-session-reaper.py --hours {thr:.0f} --reap",
        "demo": True,
    }


# ── /api/snapshots · /api/recovery · /api/diff ────────────────────────────
SNAP_TOP = 47500


def _snap_rows():
    """지난 사흘치 스냅샷 목록.

    계층적 보존이 어떤 모양인지 그대로 보인다 — 최근 6시간은 촘촘하고 그 이전은
    시간당 하나로 성겨지며, 마일스톤(★)만 영구히 남는다.
    """
    now, st = time.time(), state()
    n_win = len(st["windows"])
    n_ws = sum(len(w["workspaces"]) for w in st["windows"])
    n_cl = sum(1 for w in st["windows"] for ws in w["workspaces"] if ws["live"])
    rows = []
    for i in range(30):                       # 최근 6시간 (12분 간격으로 솎아 표현)
        t = now - i * 720
        rows.append((SNAP_TOP - i, t, n_ws - (i % 3), n_cl - (i % 4), 0, "poll"))
    for i in range(1, 72):                    # 그 이전 사흘 — 시간당 1개
        t = now - 6 * 3600 - i * 3600
        ms = 1 if i % 24 == 0 else 0
        rows.append((SNAP_TOP - 100 - i, t, n_ws - 3 - (i % 7), n_cl - 2 - (i % 5),
                     ms, "milestone" if ms else ("startup" if i % 17 == 0 else "poll")))
    return [{"id": r[0], "first_ts": r[1], "last_ts": r[1] + 600, "ts": r[1],
             "n_windows": n_win, "n_workspaces": r[2], "n_sessions": r[3],
             "is_milestone": r[4], "reason": r[5]} for r in rows]


def snapshots_payload(limit, offset, milestones, date):
    rows = _snap_rows()
    total = len(rows)
    if milestones:
        rows = [r for r in rows if r["is_milestone"]]
        total = len(rows)
    if date:
        later = [r for r in rows
                 if time.strftime("%Y-%m-%d", time.localtime(r["first_ts"])) > date]
        offset = max(0, (len(later) // limit) * limit)
    return {"snapshots": rows[offset:offset + limit], "total": total,
            "offset": offset, "limit": limit, "demo": True}


def snapshot_detail(snap_id):
    """`db.get_snapshot()` 과 같은 모양 — 화면은 `s.normalized` 를 편다."""
    p = state_payload()
    return {"id": snap_id, "ts": p["capturedAt"], "last_ts": p["capturedAt"],
            "is_milestone": 0, "reason": "poll", "normalized": p, "demo": True}


def recovery_payload(snap_id=None):
    """복구 탭 — 이 스냅샷에 들어 있던 것들.

    데모에선 지금 배치를 그대로 되비추되, **세 개는 일부러 «없는 것»으로 둔다** —
    복구 탭이 무엇을 하는 화면인지는 잃은 게 있어야 보이기 때문이다.
    """
    p = state_payload()
    gone = {s["sessionId"] for s in p["sessions"][-3:]}
    for w in p["windows"]:
        for ws in w["workspaces"]:
            c = ws.get("claude")
            if c:
                c["live"] = c["sessionId"] not in gone
                c["restored"] = False
                c["hasTarget"] = True
            for pn in ws["panes"]:
                for sf in pn["surfaces"]:
                    if sf.get("type") == "browser" and sf.get("url"):
                        sf["live"] = True
                        sf["restored"] = False
    for w in p["layoutWindows"]:
        for ws in w["workspaces"]:
            for pl in ws["panels"].values():
                if pl.get("sessionId"):
                    pl["live"] = pl["sessionId"] not in gone
                    pl["restored"] = False
                elif pl.get("url"):
                    pl["live"] = True
                    pl["restored"] = False
    return {
        "snapshotId": snap_id or SNAP_TOP, "capturedAt": p["capturedAt"],
        "lastSeen": p["capturedAt"] + 600, "stats": p["stats"],
        "windows": p["windows"], "sessions": p["sessions"],
        "layoutWindows": p["layoutWindows"], "layoutStats": p["layoutStats"],
        "nativeLayoutStats": p["layoutStats"], "sourceCheck": p["sourceCheck"],
        "liveCount": len(p["sessions"]) - len(gone), "restoredTotal": 0,
        "demo": True,
    }


def diff_payload(a):
    """스냅샷 ↔ 지금. 화면이 읽는 이름은 `removed`/`added`/`bLabel` 이다."""
    p = state_payload()
    removed = [{"sessionId": s["sessionId"], "label": s["label"],
                "workspaceTitle": s["nativeTitle"], "cwd": s["cwd"]}
               for s in p["sessions"][-3:]]
    added = [{"sessionId": s["sessionId"], "label": s["label"],
              "workspaceTitle": s["nativeTitle"], "cwd": s["cwd"]}
             for s in p["sessions"][:1]]
    return {"aLabel": f"스냅샷 {a}", "bLabel": "현재",
            "removed": removed, "added": added,
            "removedCount": len(removed), "addedCount": len(added), "demo": True}


# ── 제어 (인메모리 변형) ──────────────────────────────────────────────────
# 계약은 실제 엔드포인트와 **글자 그대로 같다**. 데모만 다른 모양을 쓰면 프런트가
# 데모에서만 동작하는 코드를 갖게 되고, 그 순간 "실물 그대로"라는 전제가 깨진다.

def _find_ws(key):
    for win in state()["windows"]:
        for ws in win["workspaces"]:
            if key in (ws["id"], ws["ref"], ws["surfaceRef"], uid("surface", ws["id"])):
                return win, ws
    return None, None


def focus(surface=None, workspace=None, window=None, activate=True):
    st = state()
    win, ws = _find_ws(surface or workspace or window or "")
    if win is None or ws is None:
        return {"ok": False, "error": "그 탭을 못 찾았습니다", "demo": True}
    for other in win["workspaces"]:
        other["selected"] = False
    ws["selected"] = True
    st["windows"].remove(win)
    st["windows"].insert(0, win)                     # 활성 창을 맨 앞으로
    for i, w in enumerate(st["windows"]):
        w["index"] = i
    return {"ok": True, "activated": bool(activate), "how": "demo", "verified": True,
            "surface": ws["surfaceRef"], "window": win["label"],
            "workspace": ws["title"], "demo": True}


def pin(workspace, pinned):
    ws = _find_ws(workspace)[1]
    if ws is None:
        return {"ok": False, "error": "워크스페이스를 못 찾았습니다", "demo": True}
    ws["pinned"] = bool(pinned)
    return {"ok": True, "action": "pin" if pinned else "unpin", "wanted": bool(pinned),
            "pinned": ws["pinned"], "verified": True, "demo": True}


def group_pin(group, pinned):
    g = next((x for x in state()["groups"].values() if x["id"] == group), None)
    if g is None:
        return {"ok": False, "error": "그룹을 못 찾았습니다", "demo": True}
    g["pinned"] = bool(pinned)
    return {"ok": True, "group": group, "wanted": bool(pinned), "pinned": g["pinned"],
            "verified": True, "demo": True}


def group_move(group, before):
    """`group` 을 `before` 그룹 **앞**에 놓는다 — 실제 엔드포인트와 같은 계약.

    실물 cmux 는 **고정된 그룹을 움직이지 않으면서 OK 만 돌려준다**. 데모도 그 성질을
    그대로 흉내 낸다 — 안 그러면 "데모에선 되는데 실물에선 안 되는" 화면을 만들게 된다.
    """
    gs = sorted(state()["groups"].values(), key=lambda g: g["order"])
    src = next((g for g in gs if g["id"] == group), None)
    dst = next((g for g in gs if g["id"] == before), None)
    if src is None or dst is None:
        return {"ok": False, "error": "그룹을 못 찾았습니다", "demo": True}
    if src["pinned"]:
        return {"ok": True, "moved": False, "pinned": True,
                "order": [g["id"] for g in gs], "demo": True}
    gs.remove(src)
    gs.insert(gs.index(dst), src)
    for i, g in enumerate(gs):
        g["order"] = i
    return {"ok": True, "moved": True, "pinned": False,
            "order": [g["id"] for g in gs], "demo": True}


def arrangement():
    """`read_arrangement()` 과 같은 모양 — 편집 모드의 정본이자 되돌리기의 원천."""
    st = state()
    windows = []
    for win in st["windows"]:
        seen, groups = set(), []
        for ws in win["workspaces"]:
            if ws["groupKey"] in seen:
                continue
            seen.add(ws["groupKey"])
            g = st["groups"][ws["groupKey"]]
            members = [w["id"] for w in win["workspaces"] if w["groupKey"] == ws["groupKey"]]
            groups.append({"id": g["id"], "name": g["name"], "color": g["color"],
                           "icon": g["icon"], "pinned": g["pinned"], "collapsed": False,
                           "anchorId": members[0], "memberIds": members})
        groups.sort(key=lambda x: st["groups"][
            next(k for k, v in st["groups"].items() if v["id"] == x["id"])]["order"])
        windows.append({
            "id": win["id"], "ref": win["ref"], "index": win["index"],
            "title": win["name"],
            "selectedWorkspaceId": next((w["id"] for w in win["workspaces"]
                                         if w["selected"]), None),
            "groups": groups, "groupWarning": None,
            "workspaces": [{"id": ws["id"], "ref": ws["ref"], "title": ws["title"],
                            "index": i, "pinned": ws["pinned"],
                            "selected": ws["selected"], "description": ws["description"],
                            "groupId": st["groups"][ws["groupKey"]]["id"],
                            "isAnchor": False,
                            "surfaces": 1 + len(ws["browser"])}
                           for i, ws in enumerate(win["workspaces"])],
        })
    return {"capturedAt": time.time(), "activeWindowRef": st["windows"][0]["ref"],
            "windows": windows, "demo": True}


def arrange(windows):
    """편집 모드가 보낸 **최종 배치 전체**를 반영한다 (ArrangeBody 와 같은 계약).

    데모에서도 실제로 움직여야 GIF 가 된다. 끌어다 놓으면 다음 폴링에 그 자리에 있다.
    """
    st = state()
    by_id = {}
    for win in st["windows"]:
        for ws in win["workspaces"]:
            by_id[ws["id"]] = ws
    target = {w.id if hasattr(w, "id") else w["id"]:
              [str(x) for x in (w.order if hasattr(w, "order") else w["order"])]
              for w in windows}

    moved = 0
    for win in st["windows"]:
        order = target.get(win["id"])
        if order is None:
            continue
        wanted = [by_id[i] for i in order if i in by_id]
        for ws in wanted:                       # 다른 창에서 온 것들을 떼어 온다
            for other in st["windows"]:
                if ws in other["workspaces"] and other is not win:
                    other["workspaces"].remove(ws)
                    moved += 1
        rest = [w for w in win["workspaces"] if w not in wanted]
        if [w["id"] for w in win["workspaces"]] != [w["id"] for w in wanted + rest]:
            moved += 1
        win["workspaces"] = wanted + rest
        # cmux 그룹 멤버십은 groupId 가 아니라 사이드바의 '연속 위치'로 정해진다.
        # 데모도 같은 규칙을 쓴다 — 위치를 옮기면 소속이 따라간다.
        cur = None
        for ws in win["workspaces"]:
            if cur is None or ws["groupKey"] == cur:
                cur = ws["groupKey"]
            else:
                cur = ws["groupKey"]
    for win in st["windows"]:
        for i, ws in enumerate(win["workspaces"]):
            ws["index"] = i
    return {"ok": True, "moved": moved, "clamped": [], "mismatches": [],
            "warnings": [], "arrangement": arrangement(), "demo": True}


def ok(**_):
    """복원 계열 — 데모에서는 성공만 알리고 실제로 만들지 않는다."""
    return {"ok": True, "demo": True,
            "message": "데모 모드입니다 — 실제 cmux 워크스페이스는 만들지 않았습니다."}


__all__ = ["ENABLED", "nav_payload", "state_payload", "health_payload",
           "sessions_health_payload", "snapshots_payload", "recovery_payload",
           "diff_payload", "snapshot_detail", "focus", "pin", "group_pin",
           "group_move", "arrangement", "arrange", "ok", "reset", "state"]

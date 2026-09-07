"""지금 어디 — 살아있는 Claude Code 탭이 '어느 창·어느 워크스페이스·어느 탭'인지 + 대화 상태.

여러 작업을 동시에 돌릴 때 "방금 나에게 답한 게 어느 탭이냐"를 못 찾는 문제를 푼다.
매핑의 뼈대는 **cmux 가 claude 를 띄울 때 심는 환경변수**다:

    claude 프로세스 ─ CMUX_PANEL_ID(=surface UUID) ─→ tree --all ─→ 창/워크스페이스/탭
                    └ --session-id(=Claude 세션 UUID) ─→ ~/.claude/projects/**/<sid>.jsonl

별도 훅 설치가 필요 없다(cmux 가 이미 자기 훅을 claude 에 자동 주입한다).
상태(작업중/권한대기/입력대기)는 두 소스를 겹쳐 읽는다:
  ① cmux 탭 제목의 선두 아이콘 — cmux 가 실시간 갱신(◑ 스피너 = 작업중, ✳ = 유휴)
  ② cmux 알림 피드(notification-feed-history) — surfaceId 별 Waiting/Permission/Completed

⚠️ 어느 소스가 비어서 판정이 안 되면 status="unknown" 으로 **드러낸다**. 빈 값을
   '정상'으로 흡수하면 유실이 무증상이 된다(대시보드의 오래된 교훈).
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

import claude_index
import cmux_client
from config import CLAUDE_PROJECTS, HOME, NATIVE_JSON

# cmux 가 탭 제목 앞에 붙이는 스피너(작업중) / 유휴 마커
SPINNER_CHARS = "◐◑◒◓◴◵◶◷⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
IDLE_MARKS = "✳✻✽✢·"
NOTIF_HISTORY = os.path.join(
    HOME, "Library", "Application Support", "cmux",
    "notification-feed-history-com.cmuxterm.app.json",
)
# Apple epoch(2001-01-01) → Unix epoch 보정치
APPLE_EPOCH_OFFSET = 978307200.0

# 우선순위 = **내가 봐야 뭐가 나아지는 순서**다. 화면 순서는 그 판단을 그대로 옮긴 것이다.
#   permission  Claude 가 내 승인을 기다리며 **멈춰 있다**. 내가 안 보면 0 이 진행된다.
#               게다가 조용하다 — 출력도 진행도 없어서 안 찾으면 영영 모른다. 최우선.
#   running     돌고 있다. 보고 싶긴 하지만 내가 없어도 진행된다.
#   waiting     턴이 끝나 프롬프트에 서 있다. **일은 끝난 상태**라 막힌 게 없다.
#   idle        오래 방치.
# (2026-08-31 permission 을 running 앞으로. 그전엔 running 이 먼저였는데, '돌고 있는 것'과
#  '나 때문에 멈춘 것' 중 뒤엣것이 더 급하다 — 전자는 기다리면 되고 후자는 기다려도 안 된다.)
# 사람이 답해야 풀리는 '막힘' 두 종류는 **같은 층**이다 — 내가 할 행동이 둘 다 "가서 답한다"로 같다.
# background = **턴은 끝났는데 뒤에서 뭔가 돌고 있는** 상태(run_in_background 셸).
#   running 보다 아래인 이유: 지금 이 순간 내가 볼 것은 없다(내가 없어도 진행된다).
#   waiting 보다 위인 이유: 그건 **완전히 멈춘** 것이고 이건 곧 다시 움직인다 —
#   게다가 끝나면 사람이 한 번 찔러 줘야 다음이 이어지므로, 그냥 대기와 섞으면 방치된다.
STATUS_ORDER = {"permission": 0, "question": 0, "running": 1, "background": 2,
                "waiting": 3, "idle": 4, "unknown": 5}
# cmux 가 '그냥 프롬프트 대기'에 쓰는 고정 본문. 이것과 다르면 질문 본문이 실린 것이다.
WAITING_GENERIC = "Claude is waiting for your input"

# STATUS_ORDER 에서 파생하는 것들 — 클라이언트가 상태 집합을 스스로 적지 않게 하려고 내려 준다.
#   STATUS_ORDER_LIST  화면에 놓는 순서(우선순위가 같으면 위 딕셔너리의 기재 순서를 따른다)
#   BLOCKED_STATUSES   '막힘' = 우선순위 0층 = 사람이 답해야만 풀리는 것들. 메뉴바 배지 숫자가 이 합이다.
STATUS_ORDER_LIST = sorted(STATUS_ORDER, key=lambda s: STATUS_ORDER[s])
BLOCKED_STATUSES = [s for s, v in STATUS_ORDER.items() if v == 0]

STATUS_LABEL = {
    "running": "작업 중",
    "permission": "권한 대기",
    "question": "질문 대기",
    "background": "뒤에서 진행",
    "waiting": "입력 대기",
    "idle": "유휴",
    "unknown": "판정 불가",
}

_CACHE = {"at": 0.0, "data": None}
# 마지막으로 성공한 cmux 트리 파생물. 트리 조회가 삐끗해도 화면이 통째로 비지 않게 한다.
_TREE_CACHE = {"at": 0.0, "idx": {}, "ws_tabs": {}, "active_sid": None,
               "active_win": None, "active_win_ref": None}
CACHE_TTL = 3.0            # 초 — 폴링(5초)보다 짧게


# ---------- ① claude 프로세스 → (세션 UUID, surface UUID) ----------

_PROC_CACHE = {"at": 0.0, "val": None}
_BG_CACHE = {"at": 0.0, "val": {}}

# '방금 끝났다' 를 알려면 **직전 상태**를 들고 있어야 한다 — 이 서버가 상태를 기억하는 유일한 곳이다.
# (다른 판정은 전부 매번 새로 계산한다. 전이는 원리상 그럴 수 없다.)
# ⚠️ 프로세스 메모리라 서버를 재시작하면 잊는다. 그때 놓치는 건 '끝난 직후 2분' 뿐이라 감수한다.
_PREV_STATUS = {}          # surfaceId → 직전 status
_BG_DONE_AT = {}           # surfaceId → 뒤에서 진행이 끝난 시각
BG_DONE_SEC = 120          # 끝난 뒤 이 시간 동안만 '방금 끝남'으로 본다


def background_shells():
    """claude pid → 그 세션 밑에서 살아 있는 셸 프로세스 수.

    Claude Code 의 `run_in_background` 셸은 claude 의 **직계 자식**으로 `/bin/zsh -c …` 형태로 남는다
    (세션 디렉토리 `…/<세션ID>/tasks/<작업ID>.output` 에 출력이 쌓인다).

    ⚠️ **전경 Bash 도 똑같이 생겼다**(실측 2026-09-04: 지금 돌고 있는 전경 명령과 백그라운드 셸이
       프로세스 목록에서 구별되지 않는다). 그래서 이 숫자만으로는 아무것도 판정하면 안 되고,
       **턴이 이미 끝난 탭**(대기)에서만 뜻이 생긴다 — 그때 살아 있는 셸은 백그라운드뿐이다.
       판정은 `decide_status` 한 곳에서만 한다.

    MCP 서버(`uv run …`)·`caffeinate`·`cmux hooks feed` 같은 상주 자식은 셸이 아니므로 세지 않는다.
    """
    now = time.time()
    if now - _BG_CACHE["at"] < 2.0:
        return _BG_CACHE["val"]
    out = {}
    try:
        raw = subprocess.run(["ps", "-eo", "ppid=,command="], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception as e:                                    # noqa: BLE001
        print(f"[nav] 백그라운드 셸 조회 실패: {e}", file=sys.stderr)
        return _BG_CACHE["val"]
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        ppid, _, cmd = line.partition(" ")
        cmd = cmd.strip()
        if cmd.startswith("/bin/zsh -c") or cmd.startswith("/bin/bash -c"):
            try:
                out[int(ppid)] = out.get(int(ppid), 0) + 1
            except ValueError:
                pass
    _BG_CACHE["at"], _BG_CACHE["val"] = now, out
    return out
PROC_TTL = 8.0             # 초 — ps -E 출력이 2.8MB/125ms 라 상시 폴링에선 따로 캐시한다


def claude_processes(use_cache=True):
    """탭에서 돌고 있는 claude 목록. [{pid, sessionId, surfaceId}], 에러 문자열.

    `ps -E` 로 환경변수까지 받아 CMUX_PANEL_ID(=surface UUID)를 읽는다.
    ⚠️ text=True 금지 — 다른 프로세스 인자의 비-UTF8 바이트로 죽는다(cmux_client 와 동일 교훈).

    ⚠️ **`--session-id`/`--resume` 가 붙은 것만 세면 안 된다.** 그 인자는 cmux 가 세션을 이어받아
       띄울 때만 붙는다. 새로 시작한 세션은 인자가 없어서 통째로 누락됐다
       (실측 2026-08-25: 사용자가 찾던 창7 탭이 목록에 아예 없었고, 제목이 비슷한 다른 창의
       탭을 눌러 엉뚱한 곳으로 이동). 그래서 **메인 프로세스 판별을 부모로 한다** —
       탭의 최상위 claude 는 부모가 로그인 셸(zsh/bash/sh/login)이고,
       서브에이전트·플러그인이 띄운 claude 는 부모가 claude 나 다른 실행기다.
       세션 UUID 는 있으면 쓰고 없으면 None (그래도 surface 만으로 이동은 된다).
    """
    now = time.time()
    if use_cache and _PROC_CACHE["val"] is not None and now - _PROC_CACHE["at"] < PROC_TTL:
        return _PROC_CACHE["val"]
    try:
        raw = subprocess.run(["ps", "-E", "-axww", "-o", "pid=,ppid=,command="],
                             capture_output=True, timeout=15).stdout or b""
    except Exception as e:                                    # noqa: BLE001
        print(f"[nav] ps 실패: {e}", file=sys.stderr)
        return [], f"ps 실행 실패: {e}"
    out = raw.decode("utf-8", "replace")

    cmd_by_pid = {}          # pid → command (부모 판별용)
    rows = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if not m:
            continue
        pid, ppid, cmd = int(m.group(1)), int(m.group(2)), m.group(3)
        cmd_by_pid[pid] = cmd
        if "/claude" not in cmd or "cmux hooks" in cmd:
            continue
        if not re.search(r"(^|\s)\S*/claude(\s|$)", cmd):
            continue
        panel = re.search(r"CMUX_PANEL_ID=([0-9A-Fa-f-]{36})", cmd)
        if not panel:                    # cmux 탭에서 돌고 있는 것만 대상
            continue
        sid = re.search(r"--(?:session-id|resume)\s+([0-9a-fA-F-]{36})", cmd)
        rows.append({"pid": pid, "ppid": ppid,
                     "sessionId": sid.group(1).lower() if sid else None,
                     "surfaceId": panel.group(1).upper()})

    # 부모가 로그인 셸인 것 = 그 탭의 최상위 claude
    shell_re = re.compile(r"(^|/)(-?zsh|-?bash|-?sh|login)(\s|$)")
    mains = [r for r in rows if shell_re.search(cmd_by_pid.get(r["ppid"], ""))]

    # ⚠️ CMUX_PANEL_ID 를 **무조건 믿으면 안 된다** (Claude Code 2.1.246, 실측 2026-09-02).
    #
    # 포크·재개된 세션은 로그인 셸이 아니라 데몬 밑에서 돈다:
    #     claude daemon run → claude --bg-pty-host → <version> --session-id <새 UUID> --fork-session
    # 그런데 그 데몬은 **처음 자기를 띄운 패널의 CMUX_PANEL_ID 를 환경에 그대로 물고** 있어서,
    # 나중에 다른 패널의 세션을 서빙해도 env 는 옛 패널을 가리킨다.
    # 실측: 세션 A 가 엉뚱하게 세션 B 의 패널로 배정됐고, 그 선점 때문에 정작 A 의 탭은
    # 제목으로 자기를 되찾으려다 "다른 탭이 이미 씀"으로 막혔다.
    # 그 결과 대화 기록을 못 읽어 활동 시각이 **알림 시각으로 대체**됐고(80분 전),
    # 그 대체가 다시 권한 알림의 신선도 검사를 무력화해 자물쇠 오판까지 이어졌다.
    #
    # → 배정을 **패널 제목으로 교차검증한다.** 세션의 aiTitle 은 cmux 탭 제목과 같은 문자열이라
    #   (claude_index 가 그 역인덱스를 이미 갖고 있다) 서로 어긋나면 env 쪽이 낡은 것이다.
    #   판단은 여기서 하지 않고 사실만 넘긴다 — 제목은 collect() 가 안다.
    #   (배정 오류는 조용한 오판을 낳으므로, 확실할 때만 고치고 아니면 그대로 둔다.)
    def _panel_of(r):
        return r["surfaceId"]

    # surface 당 하나로 정리 — 세션 UUID 를 아는 쪽을 우선하고, 그다음 먼저 뜬 것
    best = {}
    for r in sorted(mains, key=lambda x: (x["sessionId"] is None, x["pid"])):
        best.setdefault(_panel_of(r), r)

    # 같은 패널의 프로세스 중 session-id 를 가진 것들을 모아 둔다 —
    # 최상위(로그인 셸 자식)가 session-id 를 잃어버린 경우(데몬 구조)의 후보다.
    alt = {}
    for r in rows:
        if r["sessionId"]:
            alt.setdefault(r["surfaceId"], []).append(r["sessionId"])
    procs = [{"pid": r["pid"], "sessionId": r["sessionId"], "surfaceId": r["surfaceId"],
               "altSessionIds": sorted(set(alt.get(r["surfaceId"], [])))}
              for r in best.values()]
    out_val = (procs, None)
    _PROC_CACHE["at"], _PROC_CACHE["val"] = now, out_val
    return out_val


# cmux 그룹 아이콘은 SF Symbol 이름이라 그대로 웹에 내보내면 글자로 보인다 → 서버에서 이모지로 옮긴다
# (클라이언트마다 같은 표를 두면 어긋나므로 여기서 한 번만 변환한다).
# 그룹 아이콘은 **서버가 만들지 않는다** (2026-08-31).
#
# 예전엔 여기 _GICON 표가 cmux 의 SF Symbol 이름(gearshape.2.fill …)을 이모지로 바꿔
# 보냈다. 그런데 같은 표가 static/index.html 과 static/edit.js 에도 있어 **총 3벌**이었고,
# 그중 nav.py 만 서버에서 변환하는 바람에 같은 그룹이 '지금 어디' 탭에서는 이모지로,
# 편집 모드에서는 심볼 이름으로 보이는 어긋남이 났다.
#
# 지금은 심볼 이름을 **그대로** 내보내고 매핑은 static/icons.js(SYMBOL) 한 곳에서만 한다.
# 표현(이모지냐 lucide 냐)은 화면의 문제이지 수집기의 문제가 아니다.


_GRP_CACHE = {"mtime": None, "val": {}, "renamed": {}, "wscolor": {}}


def workspace_group_map():
    """workspaceId(UUID) → 그룹 {name,color,icon,pinned} 매핑. 없으면 그 워크스페이스는 그룹 밖.

    cmux 는 워크스페이스 그룹을 **라이브 트리에 실어 주지 않는다** — 네이티브 세션 JSON의
    `tabManager.workspaceGroups` 에만 있다. 전체 레이아웃을 파싱하면 무거워서(복원용 경로)
    여기서는 그룹 대조에 필요한 필드만 훑는다. 파일 mtime 으로 캐시한다.
    """
    try:
        mt = os.path.getmtime(NATIVE_JSON)
    except OSError:
        return {}
    if _GRP_CACHE["mtime"] == mt:
        return _GRP_CACHE["val"]
    _load_native(mt)
    return _GRP_CACHE["val"]


def panel_renamed_map():
    """패널 UUID → 사람이 그 패널 제목을 직접 바꿨나(bool).

    ⚠️ 이게 '판정 불가'의 원인이다(실측 2026-08-31). cmux 는 패널 제목 앞에 상태 마커
       (◐ 스피너 / ✳ 유휴)를 붙여 주는데, **사람이 그 패널 이름을 직접 바꾸면 title 을
       사람 문자열로 덮어쓰고 그 뒤로 마커를 다시 안 붙인다.** 네이티브 JSON 에
       customTitleSource = "user" 로 남는다. 그러면 이 대시보드가 읽을 '지금 상태' 신호가
       통째로 사라진다 — 탭 78개 중 6개가 그랬다.
       (워크스페이스 이름만 바꾼 건 무해하다. 마커는 패널 제목에 붙기 때문이다.)
    """
    try:
        mt = os.path.getmtime(NATIVE_JSON)
    except OSError:
        return {}
    if _GRP_CACHE["mtime"] != mt:
        _load_native(mt)
    return _GRP_CACHE["renamed"]


def workspace_color_map():
    """workspaceId(UUID) → 사용자가 그 워크스페이스에 칠한 색(#RRGGBB). 없으면 키 없음."""
    try:
        mt = os.path.getmtime(NATIVE_JSON)
    except OSError:
        return {}
    if _GRP_CACHE["mtime"] != mt:
        _load_native(mt)
    return _GRP_CACHE["wscolor"]


def _load_native(mt):
    """네이티브 세션 JSON 을 한 번만 읽어 그룹 표와 '이름 바꾼 패널' 표를 동시에 채운다."""
    try:
        with open(NATIVE_JSON, "rb") as f:
            native = json.load(f)
    except Exception as e:                                    # noqa: BLE001
        print(f"[nav] 네이티브 세션 JSON 읽기 실패(그룹 표시 생략): {e}", file=sys.stderr)
        _GRP_CACHE["mtime"], _GRP_CACHE["val"], _GRP_CACHE["renamed"] = mt, {}, {}
        _GRP_CACHE["wscolor"] = {}
        return
    out, renamed, wscolor = {}, {}, {}
    for w in native.get("windows", []):
        tm = w.get("tabManager") or {}
        gs = {g["id"]: g for g in (tm.get("workspaceGroups") or []) if g.get("id")}
        for ws in tm.get("workspaces", []):
            # 워크스페이스 **자체** 색. 그룹 색과는 다른 것이다 —
            # 실측 2026-09-02: 그룹 9개는 customColor 가 전부 비어 있고,
            # 워크스페이스는 78개 중 56개가 색을 갖고 있다(사용자가 직접 칠한 것).
            wsid0 = str(ws.get("workspaceId") or "").upper()
            if wsid0 and ws.get("customColor"):
                wscolor[wsid0] = ws["customColor"]
            for pn in (ws.get("panels") or []):
                pid = pn.get("id")
                if pid and pn.get("customTitleSource") == "user":
                    renamed[str(pid).upper()] = True
            wid = ws.get("workspaceId")
            g = gs.get(ws.get("groupId"))
            if not wid or not g:
                continue
            out[str(wid).upper()] = {
                "id": g.get("id"),
                "name": g.get("name") or "(그룹)",
                "color": g.get("customColor"),
                "icon": g.get("iconSymbol"),   # SF Symbol 이름 그대로 — 매핑은 icons.js
                "pinned": bool(g.get("isPinned")),
            }
    _GRP_CACHE["mtime"], _GRP_CACHE["val"], _GRP_CACHE["renamed"] = mt, out, renamed
    _GRP_CACHE["wscolor"] = wscolor


def _pid_alive(pid):
    """그 프로세스가 아직 살아 있나. 유령(캐시 시차) 판정용 — signal 0 은 아무 영향이 없다."""
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:       # 살아 있지만 다른 소유자
        return True
    except Exception:             # noqa: BLE001 — 판정 불가면 살아있다고 본다(경고를 지우지 않음)
        return True


# ---------- ② cmux 트리 → surface UUID 위치 인덱스 ----------

def surface_index(tree):
    """surface UUID → {창/워크스페이스/탭 위치}, 그리고 워크스페이스 UUID → 그 안의 탭 목록.

    탭 목록은 카드를 펼쳤을 때 "이 워크스페이스가 실제로 어떻게 구성돼 있나"(브라우저·일반
    터미널 포함)를 보여주는 데 쓴다 — 평소엔 claude 탭만 보여 가볍게 둔다.
    """
    idx, ws_tabs = {}, {}
    for w in tree.get("windows", []):
        wlabel = f"창 {w.get('index', 0) + 1}"
        # 창 번호만으로는 어느 물리 창인지 알기 어렵다 → 그 창이 지금 보여주는 워크스페이스를 덧붙인다
        sel_ws = next((x.get("title") for x in w.get("workspaces", [])
                       if x.get("id") == w.get("selected_workspace_id")), None)
        for ws in w.get("workspaces", []):
            wsid = str(ws.get("id") or "").upper()
            if wsid:
                ws_tabs[wsid] = [
                    {"ref": x.get("ref"), "type": x.get("type"),
                     "title": x.get("title"), "url": x.get("url"),
                     "selected": bool(x.get("selected_in_pane"))}
                    for pn2 in ws.get("panes", []) for x in pn2.get("surfaces", [])
                ]
            # 워크스페이스 안에서 탭이 놓인 순서. 화면이 cmux 사이드바와 같은 차례로
            # 그려지려면 창·워크스페이스 인덱스만으로는 모자란다(한 워크스페이스에 탭이
            # 여럿일 때 그 안의 차례를 알 수 없다).
            surf_n = 0
            for pi, pn in enumerate(ws.get("panes", [])):
                for s in pn.get("surfaces", []):
                    surf_n += 1
                    idx[str(s.get("id", "")).upper()] = {
                        "paneIndex": pi,
                        "surfaceIndex": surf_n - 1,
                        "windowId": w.get("id"), "windowRef": w.get("ref"),
                        "windowIndex": w.get("index"), "windowLabel": wlabel,
                        "windowVisible": w.get("visible"),
                        "windowShowing": sel_ws,
                        "workspaceId": ws.get("id"), "workspaceRef": ws.get("ref"),
                        "workspaceTitle": ws.get("title"),
                        "workspaceDescription": ws.get("description"),
                        "workspaceIndex": ws.get("index"),
                        "workspaceSelected": bool(ws.get("selected")),
                        "workspacePinned": bool(ws.get("pinned")),
                        "paneRef": pn.get("ref"),
                        "surfaceRef": s.get("ref"),
                        "tabRef": (s.get("ref") or "").replace("surface:", "tab:"),
                        "tabTitle": s.get("title"),
                        "selectedInPane": bool(s.get("selected_in_pane")),
                        "paneCount": len(ws.get("panes", [])),
                    }
    return idx, ws_tabs


# ---------- ③ cmux 알림 피드 → surface 별 최신 상태 ----------

def notifications_by_surface():
    """surfaceId → 그 탭의 최신 Claude 알림 {kind, subtitle, body, at, unread}."""
    try:
        with open(NOTIF_HISTORY, "rb") as f:
            items = json.load(f).get("notifications", [])
    except Exception as e:                                    # noqa: BLE001
        print(f"[nav] 알림 피드 읽기 실패: {e}", file=sys.stderr)
        return {}, f"알림 피드 읽기 실패: {e}"
    latest = {}
    for n in items:
        sid = str(n.get("surfaceId") or "").upper()
        if not sid:
            continue
        cur = latest.get(sid)
        if cur is None or (n.get("createdAt") or 0) > (cur.get("createdAt") or 0):
            latest[sid] = n
    out = {}
    for sid, n in latest.items():
        sub = n.get("subtitle") or ""
        low = sub.lower()
        body = (n.get("body") or "").strip()
        # ⚠️ subtitle='Waiting' 은 **서로 다른 두 상태를 뭉뚱그린다**(실측 2026-09-02):
        #     body == "Claude is waiting for your input"  → 그냥 프롬프트 대기(막힌 것 없음)
        #     그 밖(질문 본문이 실림)                      → AskUserQuestion 으로 **사람을 기다리며 막힘**
        #   후자는 431건 중 178건이었고, 178건 **전부** 본문에 [선택지] 대괄호가 있었다.
        #   즉 이 178번의 '막힘'이 그동안 평범한 '대기'로 보였다.
        #   막힘이라는 점에서는 도구 권한 대기와 같으므로 같은 층(자물쇠)으로 올린다 —
        #   bypassPermissions 여도 AskUserQuestion 과 훅의 yes/no 는 그대로 사람을 기다린다.
        kind = ("permission" if "permission" in low
                else ("question" if (body and body != WAITING_GENERIC) else "waiting")
                if "waiting" in low
                else "completed" if "completed" in low
                else "other")
        out[sid] = {
            "kind": kind, "subtitle": sub,
            "body": (n.get("body") or "").strip()[:400],
            "at": (n.get("createdAt") or 0) + APPLE_EPOCH_OFFSET,
            "unread": not n.get("isRead", True),
            "title": n.get("title"),
        }
    return out, None


# ---------- ④ transcript(jsonl) → 마지막 활동/마지막 사용자 프롬프트 ----------

_TRANSCRIPT_CACHE = {}       # sid → path
_INFO_CACHE = {}             # sid → (mtime, info) — 파일이 안 바뀌면 재파싱하지 않는다


# 프롬프트 미리보기 정제 규칙.
#  · _SKIP     : 애초에 사람 발화가 아닌 것(Monitor 알림·작업 완료 통지) → 통째로 버린다
#  · 나머지    : 사람 발화에 **덧붙은** 부분(훅 출력·system-reminder·첨부 표기)만 잘라내고
#                사람이 쓴 본문은 살린다. 통째로 버리면 미리보기가 빈다(2026-08-22 실측).
_SKIP = re.compile(r"Monitor event:|task-notification|local-command-stdout", re.I)

# `<task-type>artifact-watch-lifecycle</task-type>` 처럼 무엇이 깨웠는지 이름이 실려 온다.
_TASK_TYPE = re.compile(r"<task-type>\s*([\w.-]{1,60})\s*</task-type>", re.I)


def _entry_text(o):
    c = (o.get("message") or {}).get("content")
    return c if isinstance(c, str) else ""


def _is_system_wake(o):
    """사람이 아니라 시스템이 밀어 넣은 입력인가(그래서 claude 가 깨어난 것인가)."""
    if o.get("type") != "user":
        return False
    if o.get("promptSource") == "system":
        return True
    return bool(_SKIP.search(_entry_text(o)))


def _is_human_prompt(o):
    """사람이 실제로 친 프롬프트인가.

    `promptSource` 가 붙고 content 가 문자열인 것이 사람 발화다. 다만 promptSource 는
    'system' 일 수도 있어서(시스템 주입) 그것만으로는 모자라다 — 위 _is_system_wake 로 먼저 거른다.
    """
    if o.get("type") != "user" or not o.get("promptSource"):
        return False
    if o.get("promptSource") == "system":
        return False
    return bool(clean_prompt(_entry_text(o)))
_SYSREM = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>", re.I)
_HOOK_TAIL = re.compile(r"\n*\w[\w:]* hook success[\s\S]*$", re.I)
_LINE_NOISE = re.compile(r"^\s*(\[스킬 라우터\]|→ 위 후보의|\(스킬 \d+개 스캔).*$", re.M)
_IMG_TAG = re.compile(r"\[Image(?: #\d+)?:?[^\]]*\]")


def clean_prompt(txt):
    """프롬프트 미리보기용 정제. 사람 발화로 보이지 않으면 None."""
    if not txt or _SKIP.search(txt):
        return None
    txt = _SYSREM.sub(" ", txt)
    txt = _HOOK_TAIL.sub("", txt)
    txt = _LINE_NOISE.sub("", txt)
    had_img = bool(_IMG_TAG.search(txt))
    txt = _IMG_TAG.sub(" ", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) < 2:
        return "🖼 (이미지 첨부)" if had_img else None
    return ("🖼 " if had_img else "") + txt[:180]


def transcript_path(sid):
    if not sid:            # 새로 시작한 세션은 UUID 를 모를 수 있다 — 조용히 없음 처리
        return None
    hit = _TRANSCRIPT_CACHE.get(sid)
    if hit and os.path.exists(hit):
        return hit
    found = glob.glob(os.path.join(CLAUDE_PROJECTS, "*", f"{sid}.jsonl"))
    if not found:
        return None
    _TRANSCRIPT_CACHE[sid] = found[0]
    return found[0]


# 뒤에서부터 읽을 때의 청크 크기. 한 번에 메모리에 두는 양이 이만큼으로 묶인다.
_CHUNK = 256 * 1024


def transcript_info(sid, tail_bytes=None, _max_tail=2_000_000):
    """{mtime, cwd, lastPromptAt, lastPromptText, lastRole, ...}. 파일 꼬리만 읽는다.

    ⚠️ **파일 mtime 을 활동 시각으로 쓰면 안 된다.** claude 프로세스가 살아있는 세션은 대화가
       없어도 파일이 계속 touch 된다(실측 2026-08-22: 실행중 45개는 mtime-내용 괴리 중앙값
       1494분·89%가 10분 초과 / 종료된 2055개는 0.3분). 그래서 9시간 전에 멈춘 세션이 "16초 전"
       으로 최상단에 떴다. 활동 시각은 **transcript 내용의 timestamp** 로만 판단한다.

    ⚠️ 사람이 친 프롬프트는 **`promptSource` 필드가 붙고 content 가 문자열**이다.
       `promptId` 만으로 거르면 tool_result 엔트리(같은 promptId, content=list)까지 걸려
       미리보기가 통째로 빈다 — 2026-08-22 실측으로 확인.
    """
    path = transcript_path(sid)
    info = {"transcript": path, "mtime": 0, "cwd": None,
            "lastPromptAt": None, "lastPromptText": None, "lastRole": None,
            # 마지막 발화의 stop_reason. lastRole 과 짝이 되어 **턴이 끝났는지**를 말해 준다:
            #   user            프롬프트를 냈는데 아직 답이 없다      → 작업 중
            #   assistant/tool_use  도구 결과를 기다리는 중          → 작업 중
            #   assistant/end_turn  턴이 끝나 프롬프트에 서 있다     → 대기
            "lastStop": None,
            "lastMessageAt": None,   # 마지막 user/assistant 발화 = '대화' 시각(정본)
            "lastEntryAt": None,     # 마지막 엔트리(system/attachment 포함) = 세션이 뭔가 한 시각
            # ↓ "사람이 시킨 것"과 "시스템이 깨운 것"을 가른다.
            #   세션은 사람이 안 건드려도 움직인다 — 백그라운드 작업 알림(<task-notification>),
            #   Monitor 이벤트, 훅 출력이 들어오면 claude 가 깨어나 대답을 쓴다. 그걸 활동으로
            #   세면 "보지도 않은 탭이 방금 움직인 것"으로 올라온다(신고 2026-08-26).
            #   순서는 그대로 두되(진짜로 뭔가 하긴 했으므로) 화면에서 구분할 수 있게 한다.
            "lastHumanAt": None,     # 사람이 시킨 마지막 활동
            "wokenBySystem": False,  # 마지막 활동 구간이 시스템 발 인가
            "wakeKind": None}        # 무엇이 깨웠나(artifact-watch-lifecycle 등)
    if not path:
        return info
    try:
        info["mtime"] = os.path.getmtime(path)
        size = os.path.getsize(path)
    except Exception as e:                                    # noqa: BLE001
        print(f"[nav] transcript stat 실패({sid}): {e}", file=sys.stderr)
        return info

    # ⚠️ 캐시 키는 mtime 이 아니라 **size** — touch 만으로 mtime 이 바뀌므로 mtime 을 키로 쓰면
    #    내용이 그대로인데도 매번 재파싱한다(그리고 mtime 은 애초에 활동 지표가 아니다).
    cached = _INFO_CACHE.get(sid)
    if cached and cached[0] == size:
        return dict(cached[1])

    # 파일 끝에서부터 **청크 단위로 거슬러** 올라가며 필요한 것만 찾고 즉시 멈춘다.
    #
    # ⚠️ 예전엔 꼬리 N바이트를 통째로 read().decode().splitlines() 하고, 못 찾으면 N을 4배로
    #    키워 **처음부터 다시** 읽었다(200KB→800KB→3.2MB→8MB). 그러면 ①같은 데이터를 네 번
    #    읽고 ②그때마다 수만 개짜리 문자열 리스트가 생긴다. 세션 60개를 한 번 훑는 데
    #    RSS 가 16MB → 226MB 로 뛰었고(실측 2026-08-28), pm2 max_memory_restart(150MB)에
    #    걸려 **30초마다 프로세스가 죽었다**. 죽는 순간 진행 중이던 cmux 자식이 SIGINT 를
    #    받아 rc=-2 로 실패하고, 화면은 "탭 0개 + 경고 벽"이 됐다.
    # → 청크 하나(256KB)와 걸친 줄만 메모리에 둔다. 다 찾으면 바로 나가므로 대개 첫 청크로 끝난다.
    pending_at = None          # 아직 '사람이 시킨 것'으로 확정되지 않은 발화 시각
    scanned = 0
    try:
        with open(path, "rb") as f:
            pos, carry = size, b""
            while pos > 0 and scanned < _max_tail:
                stepn = min(_CHUNK, pos)
                pos -= stepn
                f.seek(pos)
                buf = f.read(stepn) + carry
                scanned += stepn
                parts = buf.split(b"\n")
                # 앞쪽이 잘렸으면 첫 조각은 다음 라운드로 넘긴다(파일 맨 앞이면 그대로 쓴다)
                carry = parts[0] if pos > 0 else b""
                head = [] if pos > 0 else parts[:1]
                if len(carry) > _CHUNK * 4:       # 비정상적으로 긴 한 줄 — 붙들고 있지 않는다
                    carry = b""
                for raw in reversed(head + parts[1:]):
                    if not raw.startswith(b"{"):
                        continue
                    try:
                        o = json.loads(raw.decode("utf-8", "replace"))
                    except Exception:                         # noqa: BLE001
                        continue
                    if o.get("isSidechain"):      # 서브에이전트 발화는 '내 대화'가 아니다
                        continue
                    if info["cwd"] is None and o.get("cwd"):
                        info["cwd"] = o["cwd"]
                    ts = _parse_ts(o.get("timestamp"))
                    if ts and info["lastEntryAt"] is None:
                        info["lastEntryAt"] = ts
                    if o.get("type") in ("user", "assistant"):
                        if info["lastRole"] is None:
                            info["lastRole"] = o["type"]
                            info["lastStop"] = (o.get("message") or {}).get("stop_reason")
                        if ts and info["lastMessageAt"] is None:
                            info["lastMessageAt"] = ts
                        # '시스템이 깨운 구간'은 건너뛴다. 그 안의 발화는 사람이 시킨 게
                        # 아니므로 후보(pending)를 버리고 앞 구간에서 다시 찾는다.
                        if info["lastHumanAt"] is None:
                            if _is_system_wake(o):
                                info["wokenBySystem"] = True
                                if info["wakeKind"] is None:
                                    txt = _entry_text(o)
                                    m = _TASK_TYPE.search(txt)
                                    info["wakeKind"] = (m.group(1) if m else
                                                        ("모니터" if "Monitor event:" in txt
                                                         else "백그라운드 알림"))
                                pending_at = None
                            elif _is_human_prompt(o):
                                info["lastHumanAt"] = pending_at or ts
                            elif ts and pending_at is None:
                                pending_at = ts
                    if (info["lastPromptText"] is None and o.get("type") == "user"
                            and o.get("promptSource")):
                        c = (o.get("message") or {}).get("content")
                        if isinstance(c, str):
                            cleaned = clean_prompt(c)
                            if cleaned:
                                info["lastPromptText"] = cleaned
                                info["lastPromptAt"] = ts
                    if (info["cwd"] and info["lastRole"] and info["lastPromptText"]
                            and info["lastMessageAt"] and info["lastHumanAt"]):
                        pos = 0                   # 다 찾았다 — 더 거슬러 올라가지 않는다
                        break
                del parts, buf
    except Exception as e:                                    # noqa: BLE001
        print(f"[nav] transcript 읽기 실패({sid}): {e}", file=sys.stderr)
        return info
    info["scannedBytes"] = scanned
    if info["lastHumanAt"] is None and pending_at:
        # 읽은 범위 안에 사람 프롬프트가 없었다 — 시스템 구간 밖의 발화까지가 우리가 아는 전부다
        info["lastHumanAt"] = pending_at
    _INFO_CACHE[sid] = (size, dict(info))
    return info


def _parse_ts(s):
    if not s:
        return None
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:                                         # noqa: BLE001
        return None


# ---------- ⑤ 상태 판정 ----------

# 이 시간 넘게 활동 근거가 없으면 알림을 '지금 상태'로 보지 않는다.
STALE_SEC = 6 * 3600

# 턴 중간(응답 대기·도구 대기)이 이보다 오래 가면 '작업 중'으로 보지 않는다.
# 15분은 실측으로 고른 값이 아니라 여유값이다 — 5분·15분·1시간 모두 같은 정확도(98.6%)가
# 나왔고, 시효를 아예 없애야만 오탐이 1건 생겼다. 즉 경계는 넉넉해도 되지만 있어야 한다.
RUNNING_MAX_SEC = 15 * 60


def decide_status(tab_title, notif, last_activity, now, act_src=None, turn=None, bg=0):
    """(status, source). `bg` 는 그 세션 밑에 살아 있는 셸 수.

    ⚠️ 백그라운드 판정을 **여기 한 곳에서만** 한다. 셸이 살아 있다는 사실은 턴이 끝났을 때만
       뜻이 있으므로(전경 명령과 구별되지 않는다), 기존 판정이 '대기'로 결론 난 뒤에 얹는다.
    """
    st, src = _decide_status(tab_title, notif, last_activity, now, act_src, turn)
    if st == "waiting" and bg > 0:
        return "background", f"{src} + 살아 있는 셸 {bg}개"
    return st, src


def _decide_status(tab_title, notif, last_activity, now, act_src=None, turn=None):
    """(status, source) — 근거를 함께 돌려준다(조용한 오판 방지).

    신호가 둘이다. **탭 제목 마커는 지금 상태**(cmux 가 매 순간 갱신)이고,
    **알림 피드는 지나간 사건의 기록**이다. 그래서 알림은 '아직 유효할 때만' 쓴다.
    """
    title = (tab_title or "").strip()
    if title and title[0] in SPINNER_CHARS:
        return "running", "탭 제목 스피너"

    stale_notif = None
    if notif and notif["kind"] in ("permission", "question", "waiting", "completed"):
        # ⚠️ '알림이 최신인가'를 last_activity 로 재면 **순환 판정**이 된다.
        #    대화 기록을 못 읽은 탭(세션 UUID 없이 시작한 세션 → sessionId None)은
        #    last_activity 가 **알림 시각 그 자체**라 언제나 '알림이 더 최신'이 된다.
        #    실측 2026-08-26: 16시간 전 권한 알림 하나가 영원히 '권한 대기'로 굳었다.
        #    → 알림과 **독립된** 활동 근거로만 신선도를 잰다(없으면 알림 자신의 나이로).
        indep = None if act_src == "알림" else (last_activity or 0)
        if notif["at"] >= (indep or 0) - 30:
            if now - (indep or notif["at"]) <= STALE_SEC:
                if notif["kind"] == "permission":
                    return "permission", "cmux 알림(권한 요청)"
                if notif["kind"] == "question":
                    return "question", "cmux 알림(질문 — 선택지 대기)"
                return "waiting", f"cmux 알림({notif['subtitle']})"
            # 오래된 알림은 '지금'이 아니다 → 살아 있는 신호(탭 제목 마커)에 넘긴다.
            # 권한 요청도 예외가 아니다: 예전에는 권한만 시효가 없어서, 이미 답한
            # 권한 요청 하나가 그 탭을 영구히 🔐 로 만들었다.
            stale_notif = notif

    if title and title[0] in IDLE_MARKS:
        if last_activity and now - last_activity > STALE_SEC:
            return "idle", "유휴 마커 + 6시간 이상 무활동"
        return "waiting", "탭 제목 유휴 마커"
    if stale_notif:
        return "idle", f"cmux 알림({stale_notif['subtitle']}) 뒤 {STALE_SEC // 3600}시간 이상 무활동"
    # ── 마커도 알림도 없다 → transcript 의 '턴 상태'로 되짚는다 ──────────────
    #
    # 왜 필요한가(실측 2026-08-31): **패널 제목을 사람이 직접 바꾸면 cmux 가 그 패널의
    # title 을 사람 문자열로 덮어쓰고 그 뒤로 상태 마커를 다시 안 붙인다.** 네이티브 세션
    # JSON 에서 확인된다 — 정상 탭은 panels[].title = "◑ mlxserve 분석 및 활용 방안",
    # 이름을 바꾼 탭은 panels[].customTitle 이 생기고 customTitleSource = "user" 가 붙으며
    # title 이 마커 없는 사람 문자열로 바뀐다. (워크스페이스 이름만 바꾼 건 무해하다 —
    # 마커는 패널 제목에 붙기 때문이다.) 탭 78개 중 6개가 이 상태였고, 그중 알림이 마침
    # 살아 있던 2개를 뺀 **4개가 근거를 하나도 못 찾아 '판정 불가'** 로 남았다.
    #
    # ⚠️ 이 신호는 **마커를 이기지 않는다.** 위의 어느 분기에도 안 걸렸을 때만 온다.
    #    실측에서 cmux 마커와 이 규칙이 엇갈리는 사례가 있었는데(마커는 스피너인데
    #    transcript 는 57분 전 end_turn), 어느 쪽이 맞는지 가릴 증거가 없었다. 살아 있는
    #    신호를 추정으로 덮는 것보다 구멍만 메우는 편이 낫다.
    #
    # 마커 있는 탭 72개로 대조한 정확도 98.6% — 특히 **오탐 0건**(유휴인데 작업중이라고
    # 우기는 경우가 없다). 유일한 불일치가 위의 그 57분 건이다.
    if turn:
        role, stop = turn
        if role == "user" or (role == "assistant" and stop == "tool_use"):
            # '작업 중'에는 시효를 건다 — 세션이 몇 시간씩 턴 중간에 머무르지 않는다.
            # 그렇게 보이면 중단됐거나 죽은 것이다(시효 없이 두면 오탐이 1건 생겼다).
            if last_activity and now - last_activity <= RUNNING_MAX_SEC:
                why = "프롬프트 뒤 응답 없음" if role == "user" else "도구 결과 대기"
                return "running", f"대화 기록({why})"
            return "idle", f"턴 중간에서 {RUNNING_MAX_SEC // 60}분 이상 멈춤"
        if role == "assistant":
            if last_activity and now - last_activity > STALE_SEC:
                return "idle", "대화 기록(턴 종료) + 6시간 이상 무활동"
            return "waiting", "대화 기록(턴 종료 — 프롬프트 대기)"

    if not title:
        return "unknown", "탭 제목·알림·대화 기록 모두 없음"
    return "unknown", "상태 마커 없음 · 대화 기록도 못 읽음"


def clean_title(t):
    t = (t or "").strip()
    while t and (t[0] in SPINNER_CHARS or t[0] in IDLE_MARKS):
        t = t[1:].strip()
    return t or "(제목 없음)"


# ---------- ⑥ 집계 ----------

def collect(use_cache=True):
    _RENAMED = panel_renamed_map()
    _WSCOLOR = workspace_color_map()
    now = time.time()
    if use_cache and _CACHE["data"] and now - _CACHE["at"] < CACHE_TTL:
        return _CACHE["data"]

    warnings = []
    procs, perr = claude_processes(use_cache=use_cache)
    if perr:
        warnings.append(perr)

    tree, idx, ws_tabs, active_sid = None, {}, {}, None
    active_win = active_win_ref = None
    tree_stale = 0
    try:
        tree = cmux_client.system_tree()
        idx, ws_tabs = surface_index(tree)
        act = tree.get("active") or {}
        active_sid = str(act.get("surface_id") or "").upper()
        active_win = act.get("window_id")
        active_win_ref = act.get("window_ref")
        _TREE_CACHE.update(at=now, idx=idx, ws_tabs=ws_tabs, active_sid=active_sid,
                           active_win=active_win, active_win_ref=active_win_ref)
    except Exception as e:                                    # noqa: BLE001
        # ⚠️ 여기서 빈 idx 로 진행하면 **모든 탭이 '위치 불명'이 되어 목록에서 통째로 빠진다**
        #    — 화면은 "표시할 Claude 탭이 없습니다" + 제외된 60여 개 ID 나열이 된다(신고
        #    2026-08-28). cmux 한 번 삐끗한 것이 화면 전체를 날릴 이유가 없다.
        #    직전에 성공한 배치를 그대로 쓰고, **낡았다는 사실만 드러낸다.**
        if _TREE_CACHE["idx"]:
            idx, ws_tabs = _TREE_CACHE["idx"], _TREE_CACHE["ws_tabs"]
            active_sid = _TREE_CACHE["active_sid"]
            active_win, active_win_ref = _TREE_CACHE["active_win"], _TREE_CACHE["active_win_ref"]
            tree_stale = int(now - _TREE_CACHE["at"])
            warnings.append(
                f"cmux 트리 조회가 실패해 {tree_stale}초 전 위치 정보를 씁니다 "
                f"— 방금 옮긴 탭은 제자리로 안 보일 수 있습니다 ({e})")
        else:
            warnings.append(f"cmux 트리 조회 실패(위치 표시 불가): {e}")

    notifs, nerr = notifications_by_surface()
    if nerr:
        warnings.append(nerr)
    groups = workspace_group_map()

    # ── 명령줄 배정을 패널 제목으로 교차검증한다 (2026-09-02) ────────────────
    #
    # CMUX_PANEL_ID 는 데몬 밑에서 도는 세션(포크·재개)에서 **낡을 수 있다** —
    # claude_processes() 의 주석 참조. 그래서 env 가 "이 패널은 세션 S" 라고 해도,
    # 그 패널의 제목이 가리키는 세션이 따로 있으면 env 쪽이 틀린 것이다.
    # 세션의 aiTitle 은 cmux 탭 제목과 같은 문자열이라 이 대조가 성립한다.
    #
    # ⚠️ 제목을 **항상** 우선하지는 않는다. 포크 형제가 aiTitle 을 공유하면 제목만으로는
    #    누구 것인지 못 가른다(2026-08-25 사고의 뿌리). 그래서 규칙은 좁게 둔다:
    #    **둘 다 있고 서로 다를 때만** 제목을 믿는다. 나머지는 기존 동작 그대로다.
    #    실측(2026-09-02 · 탭 76개): 일치 69 · 불일치 1 · ps없음+제목있음 2 · 판정불가 4.
    #    즉 이 규칙은 실제로 **틀린 배정 하나**만 걷어낸다.
    def _title_sid(pr):
        loc0 = idx.get(pr["surfaceId"])
        t = (loc0 or {}).get("tabTitle")
        if not t:
            return None
        try:
            # ⚠️ find_by_title() 이 아니라 lookup_title() 이다 — 전자는 **미스마다 전체를
            #    다시 훑는다.** 여기서는 탭 전부(터미널 제목 포함)를 대조하므로 그걸 쓰면
            #    재스캔이 연달아 터진다(실측: /api/nav fresh 0.58s → 1.1s).
            return claude_index.lookup_title(t)
        except Exception as e:                                # noqa: BLE001
            print(f"[nav] 제목→세션 조회 실패: {e}", file=sys.stderr)
            return None

    bg_map = background_shells()      # pid → 살아 있는 셸 수(턴이 끝난 탭에서만 뜻이 있다)
    for pr in procs:
        g = _title_sid(pr)
        if g and pr["sessionId"] and g != pr["sessionId"]:
            warnings_detail = f"{pr['sessionId'][:8]} → {g[:8]}"
            print(f"[nav] 배정 교정(패널 {pr['surfaceId'][:8]}): {warnings_detail}"
                  f" — CMUX_PANEL_ID 가 낡았습니다", file=sys.stderr)
            pr["sessionId"] = g
            pr["sessionIdCorrected"] = True

    # 이미 명령줄에서 확인된 세션들. 제목 추측이 이 중 하나를 다시 집으면 **버린다** —
    # 확정된 세션을 두 탭이 나눠 갖는 순간, 그 허상 위에서 상태·활동을 읽게 된다
    # (2026-08-25 '현재' 탭에서 같은 세션이 동명 워크스페이스 두 곳에 배정된 사고와 같은 뿌리).
    claimed = {p["sessionId"] for p in procs if p["sessionId"]}

    tabs = []
    for p in procs:
        sid_surface = (p["surfaceId"] or "").upper()
        loc = idx.get(sid_surface)
        sid, sid_src = p["sessionId"], ("명령줄(제목으로 교정)"
                                        if p.get("sessionIdCorrected") else "명령줄")
        if not sid:
            # 새로 시작한 세션은 명령줄에 --session-id 가 안 붙어 transcript 를 못 찾는다
            # → 활동 시각·프롬프트가 통째로 비고, 알림만 남아 오판의 뿌리가 됐다.
            #   cmux 가 대화를 보고 붙인 탭 제목(aiTitle)으로 세션을 되찾는다.
            guess = None
            try:
                guess = claude_index.find_by_title((loc or {}).get("tabTitle"))
            except Exception as e:                            # noqa: BLE001
                print(f"[nav] 제목→세션 조회 실패: {e}", file=sys.stderr)
            if guess and guess not in claimed:
                sid, sid_src = guess, "제목 추정"
                claimed.add(guess)
            elif guess:
                sid_src = "제목 추정(다른 탭이 이미 씀 → 버림)"
            else:
                sid_src = "없음"
        ti = transcript_info(sid)
        notif = notifs.get(sid_surface)
        # 활동 시각 = 실제 '대화'가 있었던 때. 파일 mtime 은 쓰지 않는다(위 transcript_info 주석).
        #   ① 마지막 user/assistant 발화  ② cmux 알림(Waiting/Permission/Completed = 실제 이벤트)
        #   ③ 둘 다 없으면 마지막 엔트리 → 그것도 없을 때만 mtime(그 사실을 source 에 드러낸다)
        cand = [("대화", ti["lastMessageAt"] or 0), ("알림", (notif or {}).get("at", 0) or 0)]
        act_src, last_activity = max(cand, key=lambda x: x[1])
        if not last_activity:
            if ti["lastEntryAt"]:
                act_src, last_activity = "기록", ti["lastEntryAt"]
            elif ti["mtime"]:
                act_src, last_activity = "파일시각(부정확)", ti["mtime"]
            else:
                act_src, last_activity = "없음", 0
        tab_title = (loc or {}).get("tabTitle") or (notif or {}).get("title")
        turn = (ti["lastRole"], ti["lastStop"]) if ti["lastRole"] else None
        # 이 패널의 상태 마커가 사라진 이유를 화면이 말할 수 있게 한다
        renamed = bool(_RENAMED.get(str(p["surfaceId"]).upper()))
        status, source = decide_status(tab_title, notif, last_activity, now, act_src, turn,
                                       bg_map.get(p["pid"], 0))
        tabs.append({
            "pid": p["pid"],
            "bgShells": bg_map.get(p["pid"], 0),
            "sessionId": sid,
            "sessionIdSource": sid_src,
            "lastHumanAt": ti["lastHumanAt"],
            "wokenBySystem": bool(ti["wokenBySystem"]),
            "wakeKind": ti["wakeKind"],
            "surfaceId": p["surfaceId"],
            "located": bool(loc),
            "isActiveTab": bool(sid_surface and sid_surface == active_sid),
            "title": tab_title,
            "cleanTitle": clean_title(tab_title),
            "status": status,
            "statusLabel": STATUS_LABEL[status],
            "statusSource": source,
            "lastActivity": last_activity or None,
            "activitySource": act_src,
            "lastMessageAt": ti["lastMessageAt"],
            "lastEntryAt": ti["lastEntryAt"],
            "fileMtime": ti["mtime"] or None,
            "lastPromptAt": ti["lastPromptAt"],
            "lastPromptText": ti["lastPromptText"],
            "lastRole": ti["lastRole"],
            "lastStop": ti["lastStop"],
            "titleRenamedByUser": renamed,
            # 질문 대기일 때만 본문을 싣는다 — 나머지 알림 본문은 고정 문구라 화면에 가치가 없다
            "askText": ((notif or {}).get("body") or None
                        if (notif or {}).get("kind") == "question" else None),
            "cwd": ti["cwd"],
            "transcript": ti["transcript"],
            "notif": notif,
            "group": groups.get(str((loc or {}).get("workspaceId") or "").upper()),
            # 워크스페이스 색 — 카드 왼쪽 바깥 띠. 없으면 클라이언트가 회색 폴백을 쓴다.
            "workspaceColor": _WSCOLOR.get(str((loc or {}).get("workspaceId") or "").upper()),
            "wsTabs": ws_tabs.get(str((loc or {}).get("workspaceId") or "").upper()) or [],
            **(loc or {}),
        })
    # 트리에서 위치를 못 찾은 것은 목록에서 뺀다 — 클릭해도 이동할 수 없고(그 탭이 없다),
    # 화면만 어지럽힌다. 대신 경고로 드러내 "조용히 사라지는" 일이 없게 한다.
    #
    # ⚠️ 다만 그 전에 **유령을 걸러낸다.** 프로세스 목록은 PROC_TTL 초 캐시되고 트리는 매번
    #    새로 읽으므로, 그 사이 탭이 닫히면 "캐시엔 있는데 트리엔 없는" 항목이 생긴다.
    #    실제로 경고에 뜨는 pid 가 매번 달라졌다(69527 → 72072) — 특정 프로세스가 남은 게
    #    아니라 시차가 만든 허상이라는 신호다. 그래서 lost 가 있으면 **ps 를 강제 재조회**하고,
    #    이미 사라진 프로세스는 조용히 버린다(경고할 일이 아니다).
    lost = [t for t in tabs if not t["located"]]
    if lost:
        ghosts = [t for t in lost if not _pid_alive(t["pid"])]
        lost = [t for t in lost if _pid_alive(t["pid"])]
        if ghosts:
            print(f"[nav] 캐시 시차로 생긴 유령 {len(ghosts)}개 무시: "
                  f"{[g['pid'] for g in ghosts]}", flush=True)
    tabs = [t for t in tabs if t["located"]]

    # ── 전이 기록: 뒤에서 진행 → 그 외 = 백그라운드가 막 끝났다 ──────────────
    # 이 순간이 **사람이 한 번 찔러 줘야 다음이 이어지는** 지점이라, 화면에서 그때만 강하게 알린다.
    for t in tabs:
        sid = t["surfaceId"]
        prev = _PREV_STATUS.get(sid)
        if prev == "background" and t["status"] != "background":
            _BG_DONE_AT[sid] = now
        _PREV_STATUS[sid] = t["status"]
        done = _BG_DONE_AT.get(sid)
        # 다시 백그라운드가 돌기 시작하면 지난 완료는 의미가 없다
        if t["status"] == "background":
            done = None
            _BG_DONE_AT.pop(sid, None)
        t["bgDoneAgo"] = round(now - done, 1) if done else None
        t["bgJustDone"] = bool(done and now - done <= BG_DONE_SEC)
    # 사라진 탭의 기록은 흘려보낸다(무한히 쌓이지 않게)
    alive = {t["surfaceId"] for t in tabs}
    for d in (_PREV_STATUS, _BG_DONE_AT):
        for k in [k for k in d if k not in alive]:
            d.pop(k, None)
    tabs.sort(key=lambda t: (STATUS_ORDER.get(t["status"], 9), -(t["lastActivity"] or 0)))

    counts = {}
    for t in tabs:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    if lost:
        # 다음 발생 때 바로 원인을 알 수 있게 진단 정보를 함께 남긴다(pid 만으로는 알 수 없었다).
        names = []
        for t in lost:
            tag = (t["sessionId"] or "")[:8] or f"pid {t['pid']}"
            names.append(f"{tag}(surface {str(t['surfaceId'])[:8]}…)")
            print(f"[nav] 탭 없음: pid={t['pid']} panel={t['surfaceId']} "
                  f"sid={t['sessionId']} title={t.get('title')!r}", flush=True)
        # ⚠️ ID 를 전부 나열하면 화면이 경고 벽이 된다(실측: 61개가 한 화면을 덮었다).
        #    사람이 화면에서 할 수 있는 일은 '몇 개나 그런지' 아는 것뿐이므로 개수와 몇 개만
        #    보이고, 진단에 필요한 전체 목록은 위에서 이미 서버 로그로 남겼다.
        head = ", ".join(names[:3])
        warnings.append(
            f"탭을 못 찾아 목록에서 제외한 claude {len(lost)}개"
            + (f" ({head}{' 외 %d개' % (len(names) - 3) if len(names) > 3 else ''})" if names else "")
            + " — 탭이 이미 닫혔는데 프로세스가 남았거나 cmux 밖에서 띄운 것입니다"
            + " (전체 목록은 서버 로그)")

    data = {
        "generatedAt": now,
        "tabs": tabs,
        "counts": counts,
        "total": len(tabs),
        "activeSurfaceId": active_sid,
        "activeWindowId": active_win,
        "activeWindowRef": active_win_ref,
        "warnings": warnings,
        "treeStaleSec": tree_stale,
        # ⚠️ 순서도 '막힘'의 정의도 STATUS_ORDER 하나에서만 파생한다. 여기 리터럴을 다시
        #    적어 두면 정본과 소리 없이 어긋난다 — 실제로 그랬다(웹의 상태 점 목록은
        #    question 이 빠진 채 돌고 있었다). 클라이언트(웹·메뉴바)가 상태 집합을
        #    스스로 적지 않도록 서버가 내려 준다.
        "statusOrder": STATUS_ORDER_LIST,
        "blockedStatuses": BLOCKED_STATUSES,
        "statusLabel": STATUS_LABEL,
    }
    _CACHE["at"], _CACHE["data"] = now, data
    return data

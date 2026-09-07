"""cmux 상태 관리 & 복구 대시보드 — FastAPI 앱."""
import json
import os
import time
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cmux_client
import db
import demo
import layout as layout_mod
import nav
import restore
import session_health
from config import PROJECT_DIR, RECOVERY_TXT
from snapshotter import Snapshotter

snap = Snapshotter()
STATIC_DIR = os.path.join(PROJECT_DIR, "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if demo.ENABLED:
        # 데모는 이 맥의 cmux 를 읽지도 건드리지도 않는다. 스냅샷터를 띄우면 남의
        # 컴퓨터에 DB 를 만들고 cmux 를 뒤진다 — 데모의 약속을 어기는 것이다.
        print("[demo] 데모 모드 — 합성 상태로 구동합니다 (cmux 를 읽지 않습니다)", flush=True)
        yield
        return
    db.init()
    # cmux 업데이트가 cmux.json 을 리셋해 socketPassword 를 날렸으면 자가복구.
    # (password 모드 + 비번 없음 = 소켓 제어 전면 차단 → "소켓 제어 비활성화")
    try:
        _healed, msg = cmux_client.self_heal_password()
        print(f"[self-heal] socketPassword: {msg}", flush=True)
        cmux_client.stash_good_password()   # 현재 정상 비번을 known-good 로 보관
    except Exception as e:                    # self-heal 실패가 앱 기동을 막지 않도록
        print(f"[self-heal] 예외(무시): {e}", flush=True)
    snap.capture(reason="startup")   # 즉시 1회
    snap.start()                      # 백그라운드 스레드
    yield
    snap.stop()


app = FastAPI(title="cmux State Dashboard", lifespan=lifespan)


@app.middleware("http")
async def _no_store_api(request, call_next):
    """API 응답은 절대 캐시하지 않는다.

    ⚠️ 실측 2026-08-26: `/api/nav` 가 브라우저에 캐시돼 **폴링이 낡은 데이터를 되풀이**했다
    (서버는 assetVersion 031 을 주는데 페이지는 006 을 들고 있었다). 폴링 화면에서 응답이
    캐시되면 "갱신되는 것처럼 보이는데 값이 안 바뀌는" 최악의 상태가 된다.
    """
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


# ---------- 조회 ----------
# 정적 자산 목록 — **여기 한 곳**이 정본이다.
# 새 CSS/JS 를 static/ 에 추가하면 반드시 여기에 넣는다. 안 넣으면 `?v=` 가 안 붙어
# 브라우저가 옛 파일을 계속 쓴다(WKWebView 는 Cache-Control: no-store 로도 캐시본을
# 쓴다 — 실측 2026-08-24). 예전에는 이 목록이 _asset_version() 과 _page() 에 각각
# 하드코딩돼 있어 "한 곳만 고치고 끝내는" 사고가 나기 쉬웠다.
ASSETS = ("theme.js", "tokens.css", "nav.css", "board.css", "icons.js", "nav.js", "edit.js",
          "cmux-mark.png", "favicon.png")


def _asset_version():
    """정적 자산의 최신 mtime. 페이지 URL 주입과 API 응답이 **같은 값**을 써야
    "지금 열려 있는 화면이 최신인가"를 클라이언트가 스스로 판단할 수 있다."""
    try:
        return int(max(os.path.getmtime(os.path.join(STATIC_DIR, f))
                       for f in ASSETS))
    except OSError:
        return 0


def _page(name):
    """HTML 을 주면서 nav.js/nav.css URL 에 **파일 mtime 버전**을 박아 준다.

    ⚠️ 브라우저 캐시가 완강하다. Cache-Control: no-store 를 붙여도 WKWebView 는 캐시본을
       계속 썼다(실측 2026-08-24: 서버 nav.css 는 32개 규칙인데 페이지가 쓰는 건 30개짜리
       구버전 → 고친 CSS/JS 가 사용자에게 영원히 반영 안 됨). URL 자체가 바뀌어야 확실히
       무효화되므로 `?v=<mtime>` 을 주입한다. 파일이 안 바뀌면 URL 도 그대로라 캐시가 살아 있다.
    """
    html = open(os.path.join(STATIC_DIR, name), encoding="utf-8").read()
    ver = _asset_version()
    for asset in ASSETS:
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={ver}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    return _page("index.html")


# 탭마다 고유 URL — 북마크/공유/새로고침이 그 탭으로 되돌아오게. 실제 전환은
# index.html 의 클라이언트 라우터가 pathname 을 읽어 처리한다(SPA 한 벌 유지).
PAGE_TABS = {"current": "live", "history": "history", "recovery": "recovery",
             "health": "health", "now": "now"}


@app.get("/now/full")
def page_now_full():
    """'지금 어디' 전체화면 단독 페이지 — 폰/보조 모니터에 띄워두는 용도."""
    return _page("now.html")


@app.get("/{page}")
def page(page: str):
    if page in PAGE_TABS:
        return _page("index.html")
    raise HTTPException(status_code=404, detail="없는 페이지")


@app.get("/api/nav")
def api_nav(fresh: bool = False):
    """지금 어디 — 살아있는 Claude 탭의 창/워크스페이스/탭 위치 + 대화 상태."""
    try:
        out = (demo.nav_payload(nav.STATUS_ORDER_LIST, nav.STATUS_LABEL,
                                nav.BLOCKED_STATUSES)
               if demo.ENABLED else nav.collect(use_cache=not fresh))
        out["assetVersion"] = _asset_version()
        return out
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"수집 실패: {e}")


@app.get("/api/health")
def health():
    if demo.ENABLED:
        return demo.health_payload(_asset_version())
    latest = db.get_latest()
    # cmux 가 실행 중에 cmux.json 의 socketPassword 를 반복적으로 지운다(파일로만 설정하면
    # cmux 자체 설정 저장이 덮어씀). 시작 시 self-heal 만으론 실행 중 유실을 못 잡으므로,
    # UI 가 주기적으로 폴링하는 health 에서 매번 자가복구 → cmux 가 언제 지우든 수 초 내 복원.
    # (비번이 멀쩡하면 self_heal 은 파일을 안 건드리고 즉시 반환 → 비용 무시 가능)
    healed = False
    try:
        healed, _msg = cmux_client.self_heal_password()
    except Exception:
        pass
    socket_ok = cmux_client.ping()
    return {
        "ok": True,
        "socket": socket_ok,          # cmux 소켓 제어 가용(제어 버튼 활성 여부)
        "controlAvailable": socket_ok,
        "selfHealed": healed,         # 이번 폴링에서 socketPassword 를 복구했는지
        "latestSnapshotId": latest["id"] if latest else None,
        "latestSnapshotAt": latest["last_ts"] if latest else None,
        "latestSessions": latest["n_sessions"] if latest else None,
        "assetVersion": _asset_version(),
    }


@app.get("/api/state")
def state():
    if demo.ENABLED:
        return demo.state_payload()
    try:
        return snap.build_live(use_cache=True)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"cmux 상태를 읽을 수 없음: {e}")


@app.get("/api/sessions/health")
def sessions_health(hours: float | None = None):
    """실행 중인 claude 세션의 유휴 상태. 판정은 claude-session-reaper 와 동일 규칙.

    조회 전용 — 여기서 종료하지 않는다(종료는 사람이 목록 확인 후 터미널에서).
    """
    if demo.ENABLED:
        return demo.sessions_health_payload(hours)
    if not session_health.available():
        return {"available": False,
                "reason": f"reaper 없음: {session_health.REAPER_PATH}"}
    try:
        return session_health.collect(hours)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"세션 상태를 읽을 수 없음: {e}")


@app.get("/api/snapshots")
def snapshots(limit: int = 200, offset: int = 0, milestones: int = 0,
              date: str | None = None):
    """스냅샷 목록(페이지네이션).

    date="YYYY-MM-DD" 를 주면 그 날짜가 시작되는 페이지의 offset 을 계산해 돌려준다
    (목록은 최신순이므로 '그 날 이후 항목 수'가 곧 건너뛸 개수).
    total 을 함께 반환해 UI 가 '1-200 / 21,278' 처럼 위치를 보여줄 수 있게 한다.
    """
    if demo.ENABLED:
        return demo.snapshots_payload(limit, offset, bool(milestones), date)
    ms = bool(milestones)
    if date:
        try:
            import datetime as _dt
            d = _dt.datetime.strptime(date, "%Y-%m-%d")
            # 그 날의 '끝'(다음날 0시) 기준으로 세어야 그 날 항목이 페이지 안에 들어온다
            day_end = (d + _dt.timedelta(days=1)).timestamp()
            skip = db.snapshot_offset_for_date(day_end, milestones_only=ms)
            offset = max(0, (skip // limit) * limit)
        except ValueError:
            raise HTTPException(status_code=400, detail="date 형식은 YYYY-MM-DD")
    return {
        "snapshots": db.list_snapshots(limit=limit, offset=offset, milestones_only=ms),
        "total": db.count_snapshots(milestones_only=ms),
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/snapshots/{snap_id}")
def snapshot(snap_id: int):
    if demo.ENABLED:
        return demo.snapshot_detail(snap_id)
    s = db.get_snapshot(snap_id)
    if not s:
        raise HTTPException(status_code=404, detail="스냅샷 없음")
    return s


@app.get("/api/recovery")
def recovery(snapshotId: int | None = None):
    """복구 뷰. 기본은 최신 스냅샷, snapshotId 지정 시 해당 스냅샷(크래시 전 상태 선택용)."""
    if demo.ENABLED:
        return demo.recovery_payload(snapshotId)
    if snapshotId:
        s = db.get_snapshot(snapshotId)
        if not s:
            raise HTTPException(status_code=404, detail="스냅샷 없음")
        norm, snap_id, last_ts = s["normalized"], s["id"], s["last_ts"]
    else:
        latest = db.get_latest()
        if not latest:
            raise HTTPException(status_code=404, detail="스냅샷 없음")
        norm, snap_id, last_ts = json.loads(latest["normalized_json"]), latest["id"], latest["last_ts"]
    # claude live = '실행중'(구조가 있어도 안 돌면 복원 대상) / browser live = '열린 URL'
    running, live_urls, tmap = _live_index()
    restored = db.restored_set()
    n_live = 0
    for w in norm.get("windows", []):
        for ws in w.get("workspaces", []):
            c = ws.get("claude")
            if c:
                c["live"] = c.get("sessionId") in running
                c["restored"] = ("c:" + (c.get("sessionId") or "")) in restored
                c["hasTarget"] = ws.get("title") in tmap   # 배정할 기존 워크스페이스 존재?
                n_live += 1 if c["live"] else 0
            for pn in ws.get("panes", []):
                for sf in pn.get("surfaces", []):
                    if sf.get("type") == "browser" and sf.get("url"):
                        sf["live"] = sf["url"] in live_urls
                        sf["restored"] = ("b:" + sf["url"]) in restored
                        n_live += 1 if sf["live"] else 0
    # 레이아웃(충실 복원용): 각 패널에 live/restored 오버레이
    lw = restore.load_layout_windows({"normalized": norm, "id": snap_id})
    for w in lw:
        for ws in w.get("workspaces", []):
            for p in ws.get("panels", {}).values():
                if p.get("sessionId"):
                    p["live"] = p["sessionId"] in running
                    p["restored"] = ("c:" + p["sessionId"]) in restored
                elif p.get("url"):
                    p["live"] = p["url"] in live_urls
                    p["restored"] = ("b:" + p["url"]) in restored
    # tree 에만 있던 창(=cmux 가 세션 JSON 에 안 남긴 창)은 lw 에 합성으로 들어와 있다.
    # 카운트는 합성분까지 포함한 '복원 가능 총량'으로 내고, 불일치는 명시적으로 알린다.
    approx = [w for w in lw if w.get("approx")]
    native_only = [w for w in lw if not w.get("approx")]
    source_check = {
        "treeWindows": len(norm.get("windows") or []),
        "nativeWindows": len(native_only),
        "treeOnlyWindows": len(approx),
        "treeOnlyWorkspaces": sum(len(w.get("workspaces") or []) for w in approx),
        "treeOnlyClaude": layout_mod.layout_stats(approx)["claude"] if approx else 0,
        "mismatch": bool(approx),
    }
    return {
        "snapshotId": snap_id,
        "capturedAt": norm.get("capturedAt"),
        "lastSeen": last_ts,
        "stats": norm.get("stats"),
        "windows": norm.get("windows"),
        "sessions": norm.get("sessions"),
        "layoutWindows": lw,
        "layoutStats": layout_mod.layout_stats(lw),
        "nativeLayoutStats": norm.get("layoutStats"),
        "sourceCheck": source_check,
        "liveCount": n_live,
        "restoredTotal": len(restored),
    }


@app.get("/api/recovery.txt", response_class=PlainTextResponse)
def recovery_txt():
    if os.path.exists(RECOVERY_TXT):
        with open(RECOVERY_TXT) as f:
            return f.read()
    return "복구 파일이 아직 없습니다."


def _sessions_of(norm):
    return {s["sessionId"]: s for s in norm.get("sessions", [])}


@app.get("/api/diff")
def diff(a: int, b: int = 0):
    """스냅샷 a 와 b(0이면 현재 라이브)의 Claude 세션 집합 비교."""
    if demo.ENABLED:
        return demo.diff_payload(a)
    sa = db.get_snapshot(a)
    if not sa:
        raise HTTPException(status_code=404, detail="스냅샷 a 없음")
    na = sa["normalized"]
    if b:
        sb = db.get_snapshot(b)
        if not sb:
            raise HTTPException(status_code=404, detail="스냅샷 b 없음")
        nb = sb["normalized"]
        b_label = f"스냅샷 {b}"
    else:
        nb = snap.build_live(use_cache=True)
        b_label = "현재(live)"
    setA, setB = _sessions_of(na), _sessions_of(nb)
    removed = [setA[k] for k in setA if k not in setB]   # a엔 있고 b엔 없음
    added = [setB[k] for k in setB if k not in setA]
    return {"aLabel": f"스냅샷 {a}", "bLabel": b_label,
            "removed": removed, "added": added,
            "removedCount": len(removed), "addedCount": len(added)}


# ---------- 안전 제어 ----------
class OpenBody(BaseModel):
    path: str


class ArrangeWindow(BaseModel):
    id: str                            # 창 UUID
    order: list[str] = []              # 그 창에 있어야 할 워크스페이스 UUID, 사이드바 순서대로


class ArrangeGroup(BaseModel):
    id: str                            # 그룹 UUID
    members: list[str] = []            # 그 그룹에 속해야 할 워크스페이스 UUID(앵커 포함)


class ArrangeBody(BaseModel):
    """편집 모드가 원하는 **최종 배치 전체**.

    한 번에 하나씩 옮기는 API 로 두지 않은 이유: 드래그 한 번이 여러 워크스페이스의
    인덱스를 동시에 바꾸고, 그룹 멤버십은 그 결과 위치로 다시 계산된다. 부분 적용은
    중간 상태를 남겨 되돌리기를 어렵게 만든다. **원하는 배치를 통째로 보내고 서버가
    현재와 대조해 필요한 조작만 하는 편이** 되돌리기(= 직전 배치를 그대로 다시 보냄)와
    같은 코드 경로를 쓰게 되어 검증하기 쉽다.
    """
    windows: list[ArrangeWindow] = []
    groups: list[ArrangeGroup] = []
    dryRun: bool = False


class FocusBody(BaseModel):
    surface: str | None = None
    workspace: str | None = None
    window: str | None = None       # 창 UUID — 주면 그 창도 앞으로(다중 창에서 확실하게)
    activate: bool = True           # cmux.app 을 macOS 최전면으로 (기본 켬)


class RestoreItem(BaseModel):
    type: str                       # 'claude' | 'browser'
    url: str | None = None          # browser
    command: str | None = None      # claude resume 명령
    cwd: str | None = None
    name: str | None = None         # 서피스 라벨
    sessionId: str | None = None    # claude 세션ID (중복감지·복구기록 키)
    # 원래 워크스페이스 컨텍스트(같은 워크스페이스 항목끼리 그룹화·충실 재구성)
    wsKey: str | None = None
    wsTitle: str | None = None
    wsPinned: bool = False
    wsDescription: str | None = None
    windowRef: str | None = None


class RestoreBody(BaseModel):
    items: list[RestoreItem]


# claude 세션은 각각 무거운 프로세스라 한 번에 최대치 제한(OOM 가드)
MAX_CLAUDE_PER_RESTORE = 8


def _item_key(it):
    """복구 기록/중복감지용 안정 키."""
    if it.type == "claude" and it.sessionId:
        return "c:" + it.sessionId
    if it.type == "browser" and it.url:
        return "b:" + it.url
    return None


# dedup 에서 제외할 일반(비고유) 제목 — 이런 이름의 워크스페이스는 존재 매칭하지 않는다.
# 셸 프롬프트 제목(`user@host:~`)은 사람마다 다르므로 리터럴로 적지 않고 아래 정규식에 맡긴다.
_GENERIC_TITLES = {"(무제)", ""}

# 유휴 셸 프롬프트 제목 패턴: `user@host:cwd` (claude 실행 중이면 세션 aiTitle 로 바뀜)
_SHELL_TITLE_RE = re.compile(r"^[\w.\-]+@[\w.\-]+")


def _is_idle_terminal(title):
    """터미널 서피스가 '유휴 셸'인가(= resume 명령을 send_text 로 주입해도 안전).

    cmux 는 터미널 제목을 포그라운드 프로그램이 정한다: claude 실행 중이면 대화 aiTitle,
    유휴 zsh 면 `<user>@<host>:<cwd>`. 후자만 idle 로 보고 주입 대상에 넣는다. 이렇게 하면
    이미 claude 가 떠 있는 터미널의 '입력창'에 명령이 텍스트로 박히는 사고를 원천 차단한다.
    """
    t = (title or "").strip()
    if not t or t in _GENERIC_TITLES:
        return True
    return bool(_SHELL_TITLE_RE.match(t))


def _live_index():
    """현재 cmux 상태.

    - running: 실제 실행중인 claude 세션ID (claude '복원됨' 판정 = 실행중)
    - urls: 현재 열린 브라우저 URL
    - tmap: 워크스페이스 제목 → {ref, termSid} (resume를 주입할 대상 워크스페이스/터미널)
      termSid = 선택된 터미널 우선, 없으면 첫 터미널.
    구조(워크스페이스/탭)는 이미 복원돼 있어도 claude는 안 돌 수 있으므로, claude 복원 대상
    판정은 '실행중'으로, 복원 동작은 '매칭 워크스페이스 터미널에 resume 주입'으로 한다.
    """
    try:
        live = snap.build_live(use_cache=True)
    except Exception:
        return set(), set(), {}
    running = {s["sessionId"] for s in live.get("sessions", []) if s.get("running")}
    urls, tmap = set(), {}
    for w in live.get("windows", []):
        for ws in w.get("workspaces", []):
            t = ws.get("title")
            sel_term = first_term = None
            idle_terms = []          # resume 주입 안전한 유휴 셸 터미널 sid (busy=claude실행중 제외)
            has_term = False
            for pn in ws.get("panes", []):
                for sf in pn.get("surfaces", []):
                    if sf.get("type") == "browser" and sf.get("url"):
                        urls.add(sf["url"])
                    elif sf.get("type") == "terminal":
                        has_term = True
                        sid = sf.get("id")
                        if first_term is None:
                            first_term = sid
                        if sf.get("selected"):
                            sel_term = sid
                        if sid and _is_idle_terminal(sf.get("title")):
                            idle_terms.append(sid)
            if t and t not in _GENERIC_TITLES:
                tmap.setdefault(t, {"ref": ws.get("ref"),
                                    "termSid": sel_term or first_term,
                                    "idleTerms": idle_terms,
                                    "hasTerm": has_term})
    return running, urls, tmap


@app.post("/api/cmux/restore")
def do_restore_items(body: RestoreBody):
    """워크스페이스 단위 충실 복원 + 중복 방지 + 복구 기록.

    - 이미 열려있는 항목(실행중 claude / 열린 URL)은 건너뛴다(중복 생성 방지).
    - 선택 항목을 원래 워크스페이스별로 묶어 원래 이름·설명·소속 창·고정으로 재구성.
    - claude 터미널 8개/회(OOM). 복원/이미열림 항목은 '복구됨'으로 기록해 숨김 대상에 추가.
    """
    if demo.ENABLED:
        return demo.ok()
    from collections import OrderedDict
    running, live_urls, tmap = _live_index()
    groups = OrderedDict()
    for it in body.items:
        groups.setdefault(it.wsKey or f"_{id(it)}", []).append(it)

    results = []
    claude_total = 0
    done_keys, done_labels = [], {}

    def _mark(it):
        k = _item_key(it)
        if k:
            done_keys.append(k); done_labels[k] = it.name

    for _key, items in groups.items():
        ws = items[0]
        target = tmap.get(ws.wsTitle) if ws.wsTitle else None

        if target:
            # 매칭되는 기존 워크스페이스 → 그 안에 resume 주입 / 브라우저 탭 추가 (새로 안 만듦)
            ref = target.get("ref")
            # ⚠️ 주입 대상은 '유휴 셸' 터미널만(idleTerms). claude 가 이미 떠 있는 터미널을
            #    재사용하면 resume 명령이 그 claude 입력창에 텍스트로 박힌다(미실행 사고). 유휴
            #    터미널이 없으면 새 탭을 만들어 거기에만 주입한다.
            term_pool = list(target.get("idleTerms") or [])
            resumed = added = already = 0
            for it in items:
                if it.type == "claude" and it.command:
                    if it.sessionId and it.sessionId in running:
                        already += 1; _mark(it); continue          # 이미 실행중
                    if claude_total >= MAX_CLAUDE_PER_RESTORE:
                        continue
                    sid = term_pool.pop(0) if term_pool else None
                    if not sid:
                        if target.get("hasTerm"):
                            # 터미널은 있는데 유휴가 없음 = 이미 claude 로 바쁨 → 이미 실행중 간주하고
                            # 스킵. (새 탭에 또 resume=중복, 또는 실행중 입력창 오타이핑 둘 다 방지)
                            already += 1; _mark(it); continue
                        try:                                        # 터미널이 아예 없는 WS만 새 탭
                            _o, sid = cmux_client.new_surface("terminal", workspace=ref, focus=False)
                        except cmux_client.CmuxError:
                            sid = None
                    if sid:
                        try:
                            cmux_client.run_in_surface(sid, it.command)
                            claude_total += 1; resumed += 1; _mark(it)
                        except cmux_client.CmuxError:
                            pass
                elif it.type == "browser" and it.url:
                    if it.url in live_urls:
                        already += 1; _mark(it); continue
                    try:
                        cmux_client.new_surface("browser", workspace=ref, url=it.url, focus=False)
                        added += 1; _mark(it)
                    except cmux_client.CmuxError:
                        pass
            if target.get("termSid"):
                try:
                    cmux_client.focus_surface(target["termSid"])
                except cmux_client.CmuxError:
                    pass
            results.append({"ok": True, "workspace": ws.wsTitle, "mode": "기존 워크스페이스에 주입",
                            "resumed": resumed, "browsersAdded": added, "already": already or None})
            continue

        # 매칭 워크스페이스 없음 → 원래 이름·창·고정으로 새 워크스페이스 생성
        surfaces, dropped = [], 0
        for it in items:
            if it.type == "claude" and it.command:
                if it.sessionId and it.sessionId in running:
                    _mark(it); continue
                if claude_total >= MAX_CLAUDE_PER_RESTORE:
                    dropped += 1; continue
                claude_total += 1
                surfaces.append({"type": "terminal", "command": it.command}); _mark(it)
            elif it.type == "browser" and it.url:
                if it.url in live_urls:
                    _mark(it); continue
                surfaces.append({"type": "browser", "url": it.url}); _mark(it)
        if not surfaces:
            if dropped:
                results.append({"ok": False, "workspace": ws.wsTitle,
                                "error": f"OOM 가드: claude {MAX_CLAUDE_PER_RESTORE}개 초과로 생략"})
            continue
        layout = json.dumps({"pane": {"surfaces": surfaces}}, ensure_ascii=False)
        title = ws.wsTitle or "restored"
        made = _make_workspace(title, ws.wsDescription, ws.windowRef, layout, ws.wsPinned)
        made.update({"workspace": title, "mode": "새 워크스페이스 생성", "surfaces": len(surfaces)})
        results.append(made)

    if done_keys:
        db.mark_restored(done_keys, done_labels)
    return {"ok": True, "workspaces": len(results),
            "restored": sum(1 for r in results if r.get("ok")),
            "markedDone": len(set(done_keys)), "results": results}


class KeysBody(BaseModel):
    keys: list[str]


@app.post("/api/restored/mark")
def restored_mark(body: KeysBody):
    db.mark_restored(body.keys)
    return {"ok": True, "count": len(db.restored_set())}


@app.post("/api/restored/unmark")
def restored_unmark(body: KeysBody):
    db.unmark_restored(body.keys)
    return {"ok": True, "count": len(db.restored_set())}


@app.post("/api/restored/clear")
def restored_clear():
    db.clear_restored()
    return {"ok": True, "count": 0}


def _make_workspace(title, description, window, layout, pinned):
    """워크스페이스 생성(+창 배치 실패 시 폴백) → 고정 적용."""
    ref = None
    try:
        _out, ref = cmux_client.new_workspace(name=title, description=description,
                                              window=window, layout=layout)
    except cmux_client.CmuxError:
        try:  # 원래 창이 사라졌으면 창 지정 없이 재시도
            _out, ref = cmux_client.new_workspace(name=title, description=description,
                                                  layout=layout)
        except cmux_client.CmuxError as e:
            return {"ok": False, "error": str(e)}
    if ref and pinned:
        try:
            cmux_client.workspace_action(ref, "pin")
        except cmux_client.CmuxError:
            pass
    return {"ok": True, "ref": ref}


@app.post("/api/cmux/restore-session")
def do_restore():
    if demo.ENABLED:
        return demo.ok()
    try:
        out = cmux_client.restore_session()
        return {"ok": True, "output": out.strip()}
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------- 레이아웃 충실 복원 ----------
class LayoutSel(BaseModel):
    wsId: str
    panelIds: list[str] | None = None   # None = 워크스페이스 전체


class RestoreLayoutBody(BaseModel):
    snapshotId: int | None = None
    selections: list[LayoutSel]
    autoRunClaude: bool = False
    target: str = "new"           # "new"=새 창 | "current"=현재 활성 창


@app.post("/api/cmux/restore-layout")
def do_restore_layout(body: RestoreLayoutBody):
    """선택 워크스페이스/패널을 스플릿 기하까지 충실 재현.

    원본 창별로 새 cmux 창 1개를 만들어 그 안에 워크스페이스들을 재구성한다.
    claude 자동 실행(기본 ON)은 레이아웃 command 로 생성 시점에 실행, 배치 상한
    MAX_CLAUDE_AUTORUN(12, OOM 가드). 이미 실행중인 세션은 미주입(이중기동 방지).
    """
    if demo.ENABLED:
        return demo.ok()
    if body.snapshotId:
        snap = db.get_snapshot(body.snapshotId)
    else:
        latest = db.get_latest()
        snap = db.get_snapshot(latest["id"]) if latest else None
    if not snap:
        raise HTTPException(status_code=404, detail="스냅샷 없음")

    lw = restore.load_layout_windows(snap)
    try:
        # ★ 오케스트레이션은 restore.restore_layout_windows 단일 구현(스킬 cmux-snapshot.py 와 공유).
        out = restore.restore_layout_windows(
            lw, [{"wsId": s.wsId, "panelIds": s.panelIds} for s in body.selections],
            target=body.target, autorun=body.autoRunClaude)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if out["doneKeys"]:
        db.mark_restored(out["doneKeys"])
    return {"ok": True, "workspaces": out["workspaces"], "restored": out["restored"],
            "groups": out["groups"], "autorunInjected": out["autorunInjected"],
            "markedDone": len(set(out["doneKeys"])), "results": out["results"]}


@app.post("/api/cmux/open")
def do_open(body: OpenBody):
    if demo.ENABLED:
        return demo.ok()
    path = os.path.expanduser(body.path)
    try:
        out = cmux_client.open_path(path)
        return {"ok": True, "output": out.strip()}
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cmux/focus")
def do_focus(body: FocusBody):
    if demo.ENABLED:
        return demo.focus(body.surface, body.workspace, body.window, body.activate)
    surface_id = body.surface
    if not surface_id and body.workspace:
        # 워크스페이스 UUID → 선택 페인의 선택 서피스 UUID 해석
        try:
            norm = snap.build_live(use_cache=True)
        except Exception as e:
            raise HTTPException(status_code=503, detail=str(e))
        for w in norm["windows"]:
            for ws in w["workspaces"]:
                if ws["id"] == body.workspace:
                    for pn in ws["panes"]:
                        for sf in pn["surfaces"]:
                            if sf.get("selected"):
                                surface_id = sf["id"]
                                break
                        if surface_id:
                            break
                    if not surface_id and ws["panes"] and ws["panes"][0]["surfaces"]:
                        surface_id = ws["panes"][0]["surfaces"][0]["id"]
    if not surface_id:
        raise HTTPException(status_code=400, detail="surface 또는 workspace 필요")
    # ── 순서가 중요하다 ────────────────────────────────────────────────
    # `open -a` 는 **앱**만 활성화하고, 어느 창을 앞에 둘지는 macOS 가 정한다(마지막 key window).
    # 그래서 앱 활성화를 마지막에 하면 cmux 내부 active 는 옳은데 화면엔 엉뚱한 창이 뜬다.
    #   ① 탭 선택 → ② 앱 최전면 → ③ 원하는 창 raise → ④ 탭 재확정
    # 마지막에 tree 로 **실제로 그 탭이 활성인지 검증**하고, 아니면 그 사실을 응답에 싣는다.
    try:
        cmux_client.focus_surface(surface_id)
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=500, detail=str(e))

    activated, how = (None, None)
    if body.activate:
        activated, how = cmux_client.activate_app()

    window_ok = None
    if body.window:
        try:
            cmux_client.focus_window(body.window)
            window_ok = True
        except cmux_client.CmuxError as e:                    # 실패해도 탭 전환 자체는 유효
            window_ok = False
            print(f"[focus] 창 포커스 실패(무시): {e}", flush=True)
    # 자기 검증 — "이동했다"고만 말하고 실제로는 안 옮겨간 상태를 만들지 않는다.
    # ⚠️ 여기서 전체 트리를 받으면 405ms 가 더 붙는다. 클릭 응답이 길어질수록 그 사이 브라우저가
    #    (cmux 가 최전면이 되면서) 백그라운드로 내려가 fetch 가 끊길 여지가 커진다 → "Failed to
    #    fetch". 검증엔 활성 서피스 하나면 충분하므로 가벼운 identify(164ms)를 쓴다.
    verified, active_now = None, None
    try:
        cur = cmux_client.focused_surface()
        active_now = cur["ref"]
        verified = str(cur["id"] or "").upper() == str(surface_id).upper()
        if not verified:      # 창 raise 로 탭 선택이 밀렸을 때만 한 번 더 확정한다
            cmux_client.focus_surface(surface_id)
            cur = cmux_client.focused_surface()
            active_now = cur["ref"]
            verified = str(cur["id"] or "").upper() == str(surface_id).upper()
    except Exception as e:                                    # noqa: BLE001
        print(f"[focus] 검증 실패(무시): {e}", flush=True)

    return {"ok": True, "surface": surface_id, "window": window_ok,
            "activated": activated, "activateVia": how,
            "verified": verified, "activeNow": active_now}


def _u(x):
    """UUID 대소문자 흔들림을 흡수한다(cmux 는 대문자, 우리 쪽은 섞여 들어온다)."""
    return str(x or "").upper()


# 한 창에서 한 번에 옮길 수 있는 워크스페이스 수의 상한. 드래그 한 번은 보통 1~3장이고,
# 수십 장이 움직인다는 건 모델이 어긋났다는 뜻이라 되돌리기가 어려워지기 전에 멈춘다.
MAX_MOVES = 40


def _plan_moves(cur, want):
    """`cur` 순서를 `want` 로 만드는 **최소한의 한 장짜리 이동**을 뽑는다.

    전량 재정렬(`reorder-workspaces --order`)을 쓰지 않는 이유가 여기 있다 —
    지금 순서를 그대로 넣어도 cmux 의 계획이 항등이 아니다(실측: 그룹 앵커가
    index 0 → 5 로 밀린다). 한 장 옮기려던 드래그가 안 건드린 것까지 흔들면
    "내가 안 한 변경"이 생기고, 그게 되돌리기의 신뢰를 깎는다.

    두 가지로 뽑아 **적은 쪽을 고른다**. 둘 다 시뮬레이션으로 결과가 want 와 같은지
    확인하고, 어긋나는 후보는 버린다(옮기는 개수보다 정확한 게 먼저다).

      ① 좌→우 그리디 — 그 자리에 있어야 할 게 없으면 끌어온다. 자리 하나를 맞추면
         그 앞은 다시 흐트러지지 않아 **항상 성립한다**. 다만 최소는 아니다
         (맨 앞 카드를 맨 뒤로 끌면 1번이면 될 걸 n-1번 옮긴다).
      ② LCS 유지 — 최장 공통 부분수열은 손대지 않고 나머지만 제자리에 끼워 넣는다.
         보통 이쪽이 최소에 닿는다.

    반환: [(workspaceId, targetIndex), ...] — 앞에서부터 순서대로 적용하면 된다.
    targetIndex 는 "그 항목을 빼낸 뒤 끼워 넣을 위치"다.
    """
    def simulate(moves):
        sim = list(cur)
        for wsid, idx in moves:
            sim.pop(sim.index(wsid))
            sim.insert(idx, wsid)
        return sim

    # ① 좌→우 그리디
    sim, greedy = list(cur), []
    for idx, wsid in enumerate(want):
        if idx < len(sim) and sim[idx] == wsid:
            continue
        sim.pop(sim.index(wsid))
        sim.insert(idx, wsid)
        greedy.append((wsid, idx))

    # ② LCS 를 그대로 두고 나머지만 끼워 넣기
    n, m = len(cur), len(want)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = (dp[i + 1][j + 1] + 1 if cur[i] == want[j]
                        else max(dp[i + 1][j], dp[i][j + 1]))
    keep, i, j = set(), 0, 0
    while i < n and j < m:
        if cur[i] == want[j]:
            keep.add(cur[i]); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    sim, lcs_moves = list(cur), []
    for idx, wsid in enumerate(want):
        if wsid in keep:
            continue
        pos = sim.index(wsid)
        # 앞 항목 바로 뒤가 제자리다(앞 항목들은 이미 want 순서로 놓여 있다)
        target = 0 if idx == 0 else sim.index(want[idx - 1]) + 1
        if pos < target:      # 자기를 먼저 빼면 뒤쪽 인덱스가 하나씩 당겨진다
            target -= 1
        if pos == target:
            continue
        sim.pop(pos)
        sim.insert(target, wsid)
        lcs_moves.append((wsid, target))

    cands = [mv for mv in (lcs_moves, greedy) if simulate(mv) == list(want)]
    if not cands:                      # 이론상 ①은 항상 성립하지만, 조용히 틀리느니 막는다
        raise HTTPException(status_code=500,
                            detail="재배치 계획을 세우지 못했습니다(이동 시뮬레이션 불일치)")
    return min(cands, key=len)


def read_arrangement():
    """지금 cmux 의 창·워크스페이스·그룹 배치를 **라이브로** 읽는다.

    편집 모드의 정본이자 되돌리기 스냅샷의 원천이다. 그룹 멤버십을 디스크의 네이티브
    세션 JSON(layoutWindows)에서 읽지 않는 게 핵심 — 그건 디바운스되어 방금 한 재배치가
    안 보이고, 그 시차가 과거에 유실을 무증상으로 만든 적이 있다.
    """
    tree = cmux_client.system_tree()
    active_ref = None
    windows = []
    for w in tree.get("windows", []):
        if w.get("active") or w.get("current"):
            active_ref = w.get("ref")
        wid = w.get("id")
        groups, gwarn = [], None
        by_ws_group = {}
        try:
            for g in (cmux_client.group_list(wid) or {}).get("groups", []):
                members = [_u(m) for m in (g.get("member_workspace_ids") or [])]
                groups.append({
                    "id": _u(g.get("id")), "name": g.get("name"),
                    "color": g.get("custom_color"), "icon": g.get("icon_symbol"),
                    "pinned": bool(g.get("is_pinned")),
                    "collapsed": bool(g.get("is_collapsed")),
                    "anchorId": _u(g.get("anchor_workspace_id")),
                    "memberIds": members,
                })
                for m in members:
                    by_ws_group[m] = _u(g.get("id"))
        except cmux_client.CmuxError as e:
            # 그룹을 못 읽었다는 사실을 숨기지 않는다 — 빈 그룹 목록으로 넘기면
            # 편집 모드가 "그룹이 없는 창"으로 오해하고 소속을 통째로 날릴 수 있다.
            gwarn = str(e)
        wss = []
        for ws in w.get("workspaces", []):
            gid = by_ws_group.get(_u(ws.get("id")))
            wss.append({
                "id": _u(ws.get("id")), "ref": ws.get("ref"),
                "title": ws.get("title"), "index": ws.get("index"),
                "pinned": bool(ws.get("pinned")), "selected": bool(ws.get("selected")),
                "description": ws.get("description"),
                "groupId": gid,
                "isAnchor": any(g["anchorId"] == _u(ws.get("id")) for g in groups),
                "surfaces": sum(len(p.get("surfaces") or []) for p in (ws.get("panes") or [])),
            })
        windows.append({
            "id": _u(wid), "ref": w.get("ref"), "index": w.get("index"),
            "title": w.get("title") or f"창 {(w.get('index') or 0) + 1}",
            "selectedWorkspaceId": _u(w.get("selected_workspace_id")),
            "groups": groups, "workspaces": wss,
            "groupWarning": gwarn,
        })
    return {"capturedAt": time.time(), "activeWindowRef": active_ref, "windows": windows}


def _position_pass(body, cur_arr, where, steps, clamped, warnings, tag=""):
    """`cur_arr` 를 원하는 순서로 만드는 이동을 창마다 실행한다.

    두 번 돌린다(호출부 참조). 한 번으로 안 되는 이유 —
    **그룹에 속한 워크스페이스는 그 그룹 블록 밖으로 못 나간다.** 실측: 개인·생활 그룹의
    막내를 콘텐츠·커뮤니티 그룹 끝(index 10)으로 옮기라고 하면 cmux 가 조용히 클램프해
    엉뚱한 자리에 놓는다. 소속을 먼저 바꿔 주면 그때는 그 자리로 갈 수 있다. 그래서
    ②위치 → ③소속 → ④위치 순으로 돌고, 보통 ④는 할 일이 없다.
    """
    for tw in body.windows:
        order = [_u(x) for x in tw.order]
        if not order:
            continue
        sim = None
        for w in cur_arr["windows"]:
            if w["id"] == _u(tw.id):
                sim = [ws["id"] for ws in w["workspaces"]]
        if sim is None:
            continue
        # ①에서 이 창으로 들어온 워크스페이스는 cur_arr 에 아직 없다 → 끝에 붙은 셈 치고 센다
        for wsid in order:
            if wsid not in sim and where.get(wsid) == _u(tw.id):
                sim.append(wsid)
        want = set(order)
        sim = [x for x in sim if x in want]          # 이 창에서 빠져나간 것은 제외
        # ⚠️ 편집 중에도 사용자는 cmux 를 계속 쓴다 — 워크스페이스가 닫히거나 다른 창으로
        #    옮겨졌을 수 있다. 그 ID 를 그대로 이동 계획에 넣으면 터진다(실측: 되돌리기가
        #    낡은 배치를 재생하다 500). 없는 것은 **빼되 조용히 빼지 않는다.**
        have = set(sim)
        gone = [x for x in order if x not in have]
        if gone:
            order = [x for x in order if x in have]
            warnings.append(f"창 {_u(tw.id)[:8]}: 워크스페이스 {len(gone)}개가 사라졌거나 "
                            f"다른 창으로 옮겨져 배치에서 제외했습니다")
        if sim == order:
            continue
        moves = _plan_moves(sim, order)
        if len(moves) > MAX_MOVES:
            raise HTTPException(
                status_code=400,
                detail=f"창 하나에서 {len(moves)}장을 옮기려 합니다(상한 {MAX_MOVES}). "
                       "한 번에 너무 많은 조작은 되돌리기를 어렵게 만듭니다 — 나눠서 적용하세요.")
        for wsid, idx in moves:
            if body.dryRun:
                steps.append(f"[계획] {wsid[:8]} → {idx}")
                continue
            raw = cmux_client.reorder_workspace(wsid, index=idx, window_id=tw.id)
            # cmux 가 클램프했으면 계획 인덱스가 요청과 다르게 나온다 → 그대로 모아 둔다.
            # (④에서 대개 해소되므로, 최종 판정은 맨 끝의 배치 대조가 한다)
            for _k, got in cmux_client.parse_reorder_plan(raw).items():
                if got != idx:
                    clamped.append({"workspace": wsid, "window": _u(tw.id),
                                    "wanted": idx, "planned": got, "pass": tag or "1"})
            steps.append(f"{tag}{wsid[:8]} → {idx}")


class PinBody(BaseModel):
    workspace: str            # 워크스페이스 UUID 또는 ref
    pinned: bool              # 원하는 최종 상태(토글이 아니라 '이렇게 만들어라')


class GroupPinBody(BaseModel):
    group: str                # 그룹 UUID
    pinned: bool              # 원하는 최종 상태


class GroupMoveBody(BaseModel):
    group: str                # 옮길 그룹 UUID
    before: str               # 이 그룹 **앞**에 놓는다


@app.post("/api/cmux/group/move")
def do_group_move(body: GroupMoveBody):
    """그룹 순서를 **cmux 실제로** 바꾼다 — 그 그룹을 `before` 그룹 앞에 놓는다.

    ⚠️ cmux 는 `after_group_id` 를 받아 주지만 실제로는 움직이지 않고, **고정된 그룹도
       움직이지 않는다**(둘 다 OK 만 돌아온다 — 실측 2026-09-02). 그래서 "됐다"를 응답으로
       믿으면 안 되고, 앵커 워크스페이스 인덱스로 **직접 대조**해야 한다. 뒤로 보내는 동작은
       호출하는 쪽에서 "뒤 그룹을 앞으로" 로 뒤집어 보낸다.
    """
    if demo.ENABLED:
        return demo.group_move(body.group, body.before)

    def order_of(gid):
        for _win, rows in cmux_client.group_layout().items():
            ids = [r[1] for r in rows]
            if gid in ids:
                return ids
        return []
    before_order = order_of(body.group)
    try:
        cmux_client.group_move(body.group, body.before)
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # 반영이 비동기라 곧바로 읽으면 옛 값이 나온다. 짧게 두 번 확인한다.
    after_order, moved = before_order, False
    for _ in range(2):
        time.sleep(0.45)
        after_order = order_of(body.group)
        if after_order and after_order != before_order:
            moved = True
            break
    pinned = None
    try:
        pinned = cmux_client.group_pinned_now(body.group)
    except Exception:                                         # noqa: BLE001
        pass
    return {"ok": True, "moved": moved, "order": after_order,
            # 안 움직인 이유로 가장 흔한 것이 '고정'이다 — 화면이 그대로 안내할 수 있게 실어 준다.
            "pinned": pinned}


@app.post("/api/cmux/group/pin")
def do_group_pin(body: GroupPinBody):
    """그룹 고정/해제. 워크스페이스 pin 과 같은 계약 — 토글이 아니라 원하는 최종 상태를 받는다.

    cmux 는 pin 과 unpin 이 **각각 단방향**이라 그대로 매핑된다(실측 2026-09-02: pin 을 두 번
    불러도 고정인 채다 — 토글이었다면 두 번째에 풀렸을 것이다).

    ⚠️ 확인은 라이브 RPC 로만 한다. `/api/nav` 가 쓰는 그룹 정보는 네이티브 세션 JSON 에서 오는데
       그쪽은 디바운스라 방금 한 변경이 한동안 안 보인다 — 그래서 "됐다"를 nav 로 확인하면 안 된다.
    """
    if demo.ENABLED:
        return demo.group_pin(body.group, body.pinned)
    try:
        (cmux_client.group_pin if body.pinned else cmux_client.group_unpin)(body.group)
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=500, detail=str(e))
    actual = None
    try:
        actual = cmux_client.group_pinned_now(body.group)
    except Exception as e:                                    # noqa: BLE001
        print(f"[group-pin] 확인 실패(무시): {e}", flush=True)
    # 원한 대로 안 됐으면 그 사실을 숨기지 않는다 — 화면이 "됐다"고만 믿으면 안 된다.
    return {"ok": True, "group": body.group, "wanted": body.pinned, "pinned": actual,
            "verified": (actual is None) or (actual == body.pinned)}


@app.post("/api/cmux/pin")
def do_pin(body: PinBody):
    """워크스페이스 고정/해제.

    ⚠️ `workspace-action` 을 그대로 뚫어 주는 범용 엔드포인트로 만들지 않는다 —
       같은 명령에 `close-others`·`close-above`·`close-below` 가 함께 들어 있어서,
       액션 이름을 화면에서 받는 순간 **워크스페이스를 무더기로 닫는 길**이 열린다.
       여기서는 pin/unpin 만 다룬다.

    토글이 아니라 원하는 최종 상태를 받는다. 토글은 화면이 알고 있는 상태가 낡았을 때
    반대로 뒤집힌다(화면은 고정으로 보이는데 이미 해제된 경우 → 다시 고정된다).
    """
    if demo.ENABLED:
        return demo.pin(body.workspace, body.pinned)
    action = "pin" if body.pinned else "unpin"
    try:
        cmux_client.workspace_action(body.workspace, action)
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # 실제로 그렇게 됐는지 확인해서 돌려준다 — "됐다"만 띄우면 사용자가 화면을 믿는다
    actual = None
    try:
        for w in cmux_client.system_tree().get("windows", []):
            for ws in w.get("workspaces", []):
                if _u(ws.get("id")) == _u(body.workspace) or ws.get("ref") == body.workspace:
                    actual = bool(ws.get("pinned"))
    except cmux_client.CmuxError:
        pass
    return {"ok": True, "action": action, "wanted": body.pinned,
            "pinned": actual, "verified": actual is not None and actual == body.pinned}


@app.post("/api/demo/reset")
def demo_reset():
    """데모에서 끌어다 놓은 배치를 처음 상태로. (데모 모드에서만 동작)"""
    if not demo.ENABLED:
        raise HTTPException(status_code=404, detail="데모 모드가 아닙니다")
    demo.reset()
    return {"ok": True}


@app.get("/api/cmux/arrangement")
def get_arrangement():
    if demo.ENABLED:
        return demo.arrangement()
    try:
        return read_arrangement()
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/cmux/arrange")
def do_arrange(body: ArrangeBody):
    """편집 모드가 만든 배치를 실제 cmux 에 반영한다.

    순서가 중요하다 — ①다른 창으로 갈 것부터 옮기고 ②창 안에서 **바뀐 것만** 한 장씩
    제자리로 옮긴 뒤 ③**그룹 소속을 다시 못 박는다**. cmux 그룹 멤버십은 groupId 가
    아니라 사이드바의 **연속 위치**로 정해지므로, 위치를 옮기면 옆 그룹 앵커가 멤버를
    흡수한다(2026-07-22 실측 사고). ③이 없으면 순서는 맞는데 소속이 틀어진다.

    ②에서 전량 재정렬(`reorder-workspaces --order`)을 쓰지 않는 이유는 `_plan_moves`
    주석 참조 — 지금 순서를 그대로 넣어도 cmux 의 계획이 항등이 아니라서, 한 장 옮기려던
    드래그가 안 건드린 워크스페이스까지 흔든다.

    ⚠️ 그리고 cmux 는 **요청 인덱스를 조용히 클램프한다**(고정 워크스페이스 블록 경계).
       실측: `--index 8` 요청이 5로, `--index 0` 요청이 6으로 바뀌면서도 출력은 `OK`.
       그래서 이동마다 계획 인덱스를 대조하고(`clamped`), 다 끝나면 배치를 통째로 다시
       읽어 원하던 것과 대조한다(`mismatches`). 어긋난 건 **돌려준다** — 성공 문구만
       띄우면 사용자가 화면을 믿고 넘어간다.
    """
    if demo.ENABLED:
        return demo.arrange(body.windows)
    steps, clamped, mismatches, warnings = [], [], [], []
    try:
        live = read_arrangement()
        where = {}          # 워크스페이스 UUID → 지금 있는 창 UUID
        for w in live["windows"]:
            for ws in w["workspaces"]:
                where[ws["id"]] = w["id"]

        # ① 창 간 이동
        for tw in body.windows:
            for wsid in tw.order:
                cur = where.get(_u(wsid))
                if cur and cur != _u(tw.id):
                    if body.dryRun:
                        steps.append(f"[계획] 창 이동 {wsid[:8]} → {tw.id[:8]}")
                    else:
                        try:
                            cmux_client.move_workspace_to_window(wsid, tw.id)
                            where[_u(wsid)] = _u(tw.id)
                            steps.append(f"창 이동 {wsid[:8]} → {tw.id[:8]}")
                        except cmux_client.CmuxError as e:
                            warnings.append(f"창 이동 실패({wsid[:8]}): {e}")

        # ② 창마다 **바뀐 것만** 한 장씩 옮긴다
        _position_pass(body, live, where, steps, clamped, warnings)

        # ③ 그룹 소속 재확정 — ②가 위치를 바꾼 뒤라야 의미가 있다.
        #    편집 모드는 **그 창의 그룹을 빠짐없이** 보내므로(멤버 0개인 그룹까지),
        #    "어느 그룹에도 없음"과 "그 그룹을 클라이언트가 몰랐음"을 구분할 수 있다.
        want_group = {}
        for g in body.groups:
            for wsid in g.members:
                want_group[_u(wsid)] = _u(g.id)
        # ★ 대조 상대는 '옮기기 전'이 아니라 '옮긴 직후'다. cmux 가 순서를 바꾸는
        #   순간 멤버십을 위치로 다시 계산하므로, 옮기기 전 상태와 비교하면 실제로
        #   흡수당한 워크스페이스를 그대로 지나친다(2026-07-22 사고가 그것이었다).
        mid = read_arrangement() if not body.dryRun else live
        mid_group = {}
        for w in mid["windows"]:
            for ws in w["workspaces"]:
                if ws["groupId"]:
                    mid_group[ws["id"]] = ws["groupId"]

        n_add = n_del = 0
        for tw in body.windows:
            for wsid in (_u(x) for x in tw.order):
                want, now = want_group.get(wsid), mid_group.get(wsid)
                if want == now:
                    continue          # 이미 맞으면 건드리지 않는다(불필요한 조작 = 새 위험)
                try:
                    if want:
                        if not body.dryRun:
                            cmux_client.group_add_workspace(want, wsid)
                        n_add += 1
                    else:
                        if not body.dryRun:
                            cmux_client.group_remove_workspace(wsid)
                        n_del += 1
                except cmux_client.CmuxError as e:
                    # 한 장이 실패했다고 나머지를 버리지 않는다. 대신 조용히 넘기지도 않는다
                    # — 마지막 배치 대조에서 어차피 드러나지만, 원인은 여기서만 알 수 있다.
                    warnings.append(f"그룹 소속 변경 실패({wsid[:8]}): {e}")
        pre = "[계획·근사] " if body.dryRun else ""
        if n_add:
            steps.append(f"{pre}그룹 소속 재확정 {n_add}개")
        if n_del:
            steps.append(f"{pre}그룹에서 제외 {n_del}개")

        # ④ 위치 2패스 — ②에서 그룹 경계에 막혔던 것, 그리고 ③의 group.add 가 워크스페이스를
        #    그룹 쪽으로 끌어당기며 흐트러뜨린 것을 여기서 맞춘다. 대개 할 일이 없다.
        if not body.dryRun and (n_add or n_del or clamped):
            arr2 = read_arrangement()
            where2 = {ws["id"]: w["id"] for w in arr2["windows"] for ws in w["workspaces"]}
            _position_pass(body, arr2, where2, steps, clamped, warnings, tag="재조정 ")

        # ⑤ 적용 결과를 다시 읽어 원하던 것과 대조한다
        after = read_arrangement() if not body.dryRun else live
        if not body.dryRun:
            by_win = {w["id"]: w for w in after["windows"]}
            for tw in body.windows:
                w = by_win.get(_u(tw.id))
                if not w:
                    mismatches.append({"kind": "window", "id": _u(tw.id),
                                       "detail": "적용 후 이 창을 찾지 못했습니다"})
                    continue
                actual = [ws["id"] for ws in w["workspaces"]]
                want = [_u(x) for x in tw.order]
                if actual != want:
                    moved = [i for i, (a, b) in enumerate(zip(actual, want)) if a != b]
                    mismatches.append({
                        "kind": "order", "id": _u(tw.id), "title": w["title"],
                        "detail": f"순서가 다릅니다 (처음 어긋난 위치 {moved[0] if moved else len(actual)})",
                        "wanted": want, "actual": actual})
            live_groups = {}
            for w in after["windows"]:
                for g in w["groups"]:
                    live_groups[g["id"]] = g
            for g in body.groups:
                lg = live_groups.get(_u(g.id))
                if not lg:
                    mismatches.append({"kind": "group", "id": _u(g.id),
                                       "detail": "적용 후 이 그룹이 사라졌습니다"})
                    continue
                want, actual = set(_u(x) for x in g.members), set(lg["memberIds"])
                if want != actual:
                    mismatches.append({
                        "kind": "group", "id": _u(g.id), "title": lg.get("name"),
                        "detail": "그룹 멤버가 다릅니다 — 옆 그룹이 흡수했을 수 있습니다",
                        "missing": sorted(want - actual), "extra": sorted(actual - want)})

            # 중간에 관측한 클램프는 ④에서 대개 풀린다. **끝까지 남은 것만** 보고한다 —
            # 이미 해소된 걸 경고로 띄우면 경고가 소음이 되고, 소음이 된 경고는 안 읽힌다.
            want_pos, actual_pos = {}, {}
            for tw in body.windows:
                for i, wsid in enumerate(_u(x) for x in tw.order):
                    want_pos[wsid] = (_u(tw.id), i)
            for w in after["windows"]:
                for i, ws in enumerate(w["workspaces"]):
                    actual_pos[ws["id"]] = (w["id"], i)
            still, seen = [], set()
            for c in clamped:
                wsid = c["workspace"]
                if wsid in seen:
                    continue
                seen.add(wsid)
                if want_pos.get(wsid) != actual_pos.get(wsid):
                    still.append({"workspace": wsid,
                                  "window": (actual_pos.get(wsid) or ("", 0))[0],
                                  "wanted": (want_pos.get(wsid) or ("", -1))[1],
                                  "planned": (actual_pos.get(wsid) or ("", -1))[1]})
            clamped = still

        return {"ok": True, "dryRun": body.dryRun, "steps": steps,
                "clamped": clamped, "mismatches": mismatches,
                "warnings": warnings, "arrangement": after}
    except HTTPException:
        raise
    except cmux_client.CmuxError as e:
        raise HTTPException(status_code=500,
                            detail=f"{e} (여기까지 진행: {', '.join(steps) or '없음'})")
    except Exception as e:                                        # noqa: BLE001
        # 이유를 안 붙인 500 은 화면에서 "실패"라는 사실만 남기고 원인을 지운다.
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {e} (여기까지 진행: {', '.join(steps) or '없음'})")


# 정적 파일 (있으면)
class NoCacheStatic(StaticFiles):
    """정적 자산을 항상 재검증시킨다.

    ⚠️ 기본 StaticFiles 는 Cache-Control 을 안 붙여서 브라우저가 heuristic 캐싱으로
       **구버전 nav.js/nav.css 를 계속 쓴다**(실측 2026-08-24: 서버엔 새 코드가 있는데
       페이지가 받은 건 구버전 → 고친 동작이 사용자에게 반영 안 됨. `goto` 로는 캐시된
       스크립트가 계속 실행되고 `reload` 를 해야 갱신됐다).
       ⚠️ `no-cache`(재검증) 로는 부족했다 — WKWebView 가 재검증을 건너뛰고 캐시본을 썼다
       (CSS 규칙이 0개로 잡힘). 로컬 대시보드라 대역폭이 문제되지 않으므로 `no-store` 로
       캐시 자체를 막는다.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp


if os.path.isdir(STATIC_DIR):
    app.mount("/static", NoCacheStatic(directory=STATIC_DIR), name="static")



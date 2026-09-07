"""Claude Code 세션(.jsonl) 라벨/cwd/활동 추출기.

세션ID → { label, cwd, lastActivity, msgCount, exists, jsonlPath, projectDir }
(파일 mtime 기준 캐시. 스냅샷터가 활성 세션들에 대해서만 호출.)
"""
import glob
import json
import os
import re
import time
import unicodedata
from shlex import quote as _shq

from config import CLAUDE_PROJECTS

# resume 명령 앞에 cmux가 박아둔 `cd '<경로>' && ...` 프리픽스
_CD_PREFIX = re.compile(r"^\s*cd\s+'((?:[^'\\]|\\.)*)'\s*&&\s*")

# 세션ID → jsonl 경로 (전체 인덱스, TTL 캐시)
_path_index = {}
_path_index_ts = 0.0
_PATH_TTL = 20  # 초

# 세션ID → (mtime, meta) 캐시
_meta_cache = {}

_MAX_SCAN_LINES = 500  # 라벨/cwd 추출용 상단 스캔 한도


def _refresh_path_index(force=False):
    global _path_index, _path_index_ts
    now = time.time()
    if not force and (now - _path_index_ts) < _PATH_TTL and _path_index:
        return
    idx = {}
    for p in glob.glob(os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")):
        sid = os.path.basename(p)[:-6]
        # 같은 세션이 여러 폴더에 있으면 최신(mtime 큰) 것 채택
        prev = idx.get(sid)
        if prev is None or os.path.getmtime(p) > os.path.getmtime(prev):
            idx[sid] = p
    _path_index = idx
    _path_index_ts = now


def find_jsonl(session_id):
    _refresh_path_index()
    p = _path_index.get(session_id)
    if p and os.path.exists(p):
        return p
    # 캐시 미스 시 강제 갱신 한 번
    _refresh_path_index(force=True)
    return _path_index.get(session_id)


def _count_lines(path):
    try:
        n = 0
        with open(path, "rb") as f:
            for _ in f:
                n += 1
        return n
    except OSError:
        return 0


def _extract(path):
    ai_title = summary = first_user = cwd = None
    try:
        with open(path, "r", errors="replace") as f:
            for i, line in enumerate(f):
                if i > _MAX_SCAN_LINES and (ai_title or summary) and cwd:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = o.get("type")
                if t == "ai-title" and not ai_title:
                    ai_title = (o.get("aiTitle") or "").strip()
                elif t == "summary" and not summary:
                    summary = (o.get("summary") or "").strip()
                if cwd is None and isinstance(o.get("cwd"), str):
                    cwd = o["cwd"]
                if first_user is None and t == "user":
                    c = o.get("message", {}).get("content")
                    txt = None
                    if isinstance(c, list):
                        for part in c:
                            if isinstance(part, dict) and part.get("type") == "text":
                                txt = part.get("text", "")
                                break
                    elif isinstance(c, str):
                        txt = c
                    if txt:
                        txt = " ".join(txt.split())
                        if txt and not txt.startswith("<"):
                            first_user = txt
    except OSError:
        pass
    label = ai_title or summary or first_user or "(제목 없음)"
    if len(label) > 90:
        label = label[:89] + "…"
    return label, cwd


# 세션ID → (mtime, launch_cwd) 캐시
_launch_cache = {}


def _enc(p):
    """Claude Code 프로젝트 폴더 인코딩(cli.js와 동일): NFC 정규화 후 비영숫자→'-'."""
    return re.sub(r"[^a-zA-Z0-9]", "-", unicodedata.normalize("NFC", p))


def resolve_launch_cwd(session_id):
    """세션의 올바른 launch cwd 복원.

    cmux resume 바인딩에 박힌 `cd '<경로>'` 는 '바인딩 캡처 순간의 (drift된) 터미널 cwd'라,
    프로젝트 폴더가 이동/rename되면 그 경로가 사라져 resume가 깨진다. resume는 실제로는
    프로젝트 폴더(~/.claude/projects/<enc(launch_cwd)>)에 바인딩되므로, 세션 .jsonl에서
    '폴더명 인코딩과 일치하는 cwd'(=진짜 launch cwd)를 복원한다.
    (claude-resume-cwd-fix.sh 와 동일 규칙 — 대시보드가 주입 전에 미리 교정하기 위함.)

    반환: 존재하는 디렉토리 경로(str). 못 구하면 None.
    """
    if not session_id:
        return None
    path = find_jsonl(session_id)
    if not path:
        return None
    mtime = os.path.getmtime(path)
    cached = _launch_cache.get(session_id)
    if cached and cached[0] == mtime:
        return cached[1]
    folder = os.path.basename(os.path.dirname(path))
    launch = first_dir = None
    try:
        with open(path, "r", errors="replace") as f:
            for i, line in enumerate(f):
                if i > _MAX_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                c = o.get("cwd")
                if not isinstance(c, str):
                    continue
                if _enc(c) == folder and os.path.isdir(c):
                    launch = c            # 폴더 인코딩 매칭 = 진짜 launch cwd
                    break
                if first_dir is None and os.path.isdir(c):
                    first_dir = c         # 폴백: 처음 등장한 실재 cwd
    except OSError:
        pass
    launch = launch or first_dir
    _launch_cache[session_id] = (mtime, launch)
    return launch


def correct_resume(session_id, command, cwd=None):
    """stale해진 resume 명령/ cwd를 세션의 실제 launch cwd 기준으로 교정.

    cmux가 저장한 `cd '<경로>' && ...` 프리픽스는 바인딩 캡처 순간의 (drift된) 폴더라,
    그 폴더가 이동/rename되면 cd 실패 → `&&` 단락 → 뒤의 cwd-fix wrapper 조차 미실행 →
    resume가 깨진다. 프리픽스를 `cd '<resolved>' 2>/dev/null; ...` 로 교체해
    경로를 교정하고(그래도 어긋나도) 뒤 명령은 반드시 실행되게 한다.

    반환: (command, cwd) — 교정 불가하면 원본 유지.
    """
    resolved = resolve_launch_cwd(session_id) if session_id else None
    if resolved:
        cwd = resolved
    if command:
        m = _CD_PREFIX.match(command)
        if m:
            old, rest = m.group(1), command[m.end():]
            if (resolved and resolved != old) or not os.path.isdir(old):
                target = resolved or old
                command = f"cd {_shq(target)} 2>/dev/null; {rest}"
    return command, cwd


def session_meta(session_id):
    path = find_jsonl(session_id)
    if not path:
        return {
            "sessionId": session_id, "label": "(기록 없음)", "cwd": None,
            "lastActivity": None, "msgCount": 0, "exists": False,
            "jsonlPath": None, "projectDir": None,
        }
    mtime = os.path.getmtime(path)
    cached = _meta_cache.get(session_id)
    if cached and cached[0] == mtime:
        return cached[1]
    label, cwd = _extract(path)
    meta = {
        "sessionId": session_id,
        "label": label,
        "cwd": cwd,
        "lastActivity": mtime,
        "msgCount": _count_lines(path),
        "exists": True,
        "jsonlPath": path,
        "projectDir": os.path.basename(os.path.dirname(path)),
    }
    _meta_cache[session_id] = (mtime, meta)
    return meta


# ---------- 제목 → 세션ID 역인덱스 ----------
# 왜 필요한가: 소켓 tree 에만 존재하는(=네이티브 세션 JSON 에 저장되지 않은) 창은
# resume 바인딩이 없어 sessionId 를 알 수 없다. 하지만 cmux 터미널 서피스의 제목은
# claude 의 aiTitle 과 같으므로, 제목으로 세션을 되찾을 수 있다.
# (2026-08-04 사고: 창 하나가 하루 종일 네이티브에 기록되지 않은 채 재부팅으로 소실.
#  스냅샷의 tree 쪽에는 제목이 남아 있어 이 역매칭으로 세션을 전부 복원했다.)

_title_index = {}          # 정규화제목 → (sid, mtime)
_title_miss = set()        # 이번 인덱스 세대에서 매칭 실패한 제목(재스캔 폭주 방지)
_title_index_ts = 0.0
_TITLE_TTL = 60            # 초
_TITLE_MAX_AGE_DAYS = 45   # 이보다 오래 방치된 세션은 인덱싱하지 않음
_AI_TITLE_RE = re.compile(rb'"aiTitle"\s*:\s*"((?:[^"\\]|\\.)*)"')
# cmux 가 터미널 제목 앞에 붙이는 상태 표식(✳ = claude 실행중) + 유사 기호
_TITLE_PREFIX = re.compile(r"^[\s✳✻✽*·•►▶\-–—]+")


def normalize_title(t):
    """제목 매칭 키: 상태 표식 제거 + NFC + 공백 접기 + 소문자."""
    if not t:
        return ""
    t = unicodedata.normalize("NFC", str(t))
    t = _TITLE_PREFIX.sub("", t)
    return " ".join(t.split()).casefold()


def _refresh_title_index(force=False):
    global _title_index, _title_index_ts
    now = time.time()
    if not force and (now - _title_index_ts) < _TITLE_TTL and _title_index:
        return
    cutoff = now - _TITLE_MAX_AGE_DAYS * 86400
    idx = {}
    for p in glob.glob(os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")):
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        title = None
        try:
            with open(p, "rb") as f:
                for i, line in enumerate(f):
                    if i > _MAX_SCAN_LINES:
                        break
                    m = _AI_TITLE_RE.search(line)
                    if m:
                        title = m.group(1)
                        break
        except OSError:
            continue
        if not title:
            continue
        try:
            title = json.loads(b'"' + title + b'"')
        except (json.JSONDecodeError, ValueError):
            title = title.decode("utf-8", "replace")
        key = normalize_title(title)
        if not key:
            continue
        prev = idx.get(key)
        # 같은 제목이 여럿이면 가장 최근 활동한 세션을 채택
        if prev is None or mtime > prev[1]:
            idx[key] = (os.path.basename(p)[:-6].lower(), mtime)
    _title_index = idx
    _title_index_ts = now
    _title_miss.clear()      # 새 세대 — 실패 기록 리셋


def lookup_title(title):
    """aiTitle → sessionId. **미스여도 재스캔하지 않는다.**

    find_by_title() 은 못 찾으면 전체를 다시 훑는다(새로 시작한 세션을 잡기 위해서다).
    그 대가가 커서, '있으면 좋고 없으면 그만'인 대조용으로는 못 쓴다 — 터미널 제목
    (joon@host:~ 같은 것)이 섞인 탭 수십 개를 한 번에 조회하면 재스캔이 연달아 터진다
    (실측 2026-09-02: /api/nav fresh 0.58s → 1.1s). 대조는 이 함수를 쓴다.
    """
    key = normalize_title(title)
    if not key:
        return None
    _refresh_title_index()
    hit = _title_index.get(key)
    return hit[0] if hit else None


def find_by_title(title):
    """aiTitle(터미널 서피스 제목) → sessionId. 못 찾으면 None."""
    key = normalize_title(title)
    if not key:
        return None
    _refresh_title_index()
    hit = _title_index.get(key)
    if hit:
        return hit[0]
    # 'INBOX'·'joon@host:~' 처럼 claude 가 아닌 터미널 제목은 영영 매칭되지 않는다.
    # 미스마다 전체 재스캔하면 워크스페이스 수만큼 재스캔이 터진다(실측 0.78s/스냅샷) →
    # 세대별 네거티브 캐시로 재스캔을 미스당 1회로 제한.
    if key in _title_miss:
        return None
    _refresh_title_index(force=True)   # 방금 생긴 세션 대비 1회 강제 갱신
    hit = _title_index.get(key)
    if hit:
        return hit[0]
    _title_miss.add(key)
    return None


# cmux 는 터미널마다 CMUX_CLAUDE_WRAPPER_SHIM(세션 추적용 래퍼)을 심어두고 resume 바인딩에서
# 그것을 우선 실행한다. 합성 명령도 같은 규칙을 따라야 cmux 통합이 유지된다(없으면 `claude` 폴백).
_CLAUDE_BIN = ('"$([ -x "${CMUX_CLAUDE_WRAPPER_SHIM:-}" ] && '
               'printf \'%s\' "$CMUX_CLAUDE_WRAPPER_SHIM" || printf claude)"')


def build_resume(session_id, cwd=None, skip_permissions=True):
    """sessionId → cmux 터미널에서 실행할 resume 명령.

    네이티브 바인딩이 없어 명령 문자열 자체가 없는 경우(tree-only 창)에 쓴다.
    cd 는 `;` 로 이어 붙인다 — 실패해도 뒤의 resume 은 반드시 실행되도록(`&&` 단락 방지).

    skip_permissions: cmux 가 저장하는 네이티브 resume 바인딩에는 항상
        `--dangerously-skip-permissions` 가 붙어 있다. 이걸 빼면 복원된 claude 가 권한 확인
        모드로 떠서 매 도구 호출마다 멈춘다 — 복원의 목적(하던 작업 그대로 이어가기)을 깬다.
        따라서 합성 복원도 기본으로 붙여 네이티브와 동작을 일치시킨다.
    """
    if not session_id:
        return None, cwd
    target = resolve_launch_cwd(session_id) or cwd
    cmd = f"{_CLAUDE_BIN} --resume {_shq(session_id)}"
    if skip_permissions:
        cmd += " --dangerously-skip-permissions"
    if target:
        cmd = f"cd -- {_shq(target)} 2>/dev/null; {cmd}"
    return cmd, target

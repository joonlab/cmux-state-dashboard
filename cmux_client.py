"""cmux CLI/소켓 RPC 래퍼.

읽기: system.tree (전체 라이브 구조). 안전 제어: restore-session / open / focus.
이벤트: `cmux events` 스트림을 라인 단위 JSON 제너레이터로 노출.

주의: `surface.resume.get` 은 surface 인자를 무시하고 caller 바인딩만 반환하므로
      resume 명령 조인에는 사용하지 않는다(대신 hook/native json 사용 — snapshotter 참고).
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

from config import (
    CMUX_BIN, CMUX_CONFIG_JSON, CMUX_CONFIG_DIR, CMUX_PW_STASH,
    CMUX_SOCKET_FILE, CMUX_LAST_SOCKET, CMUX_SOCKET_XDG,
)


class CmuxError(Exception):
    pass


def _load_jsonc(path) -> dict:
    """JSONC(줄머리 // 주석 + 트레일링 콤마) 파일을 dict 로 로드. 실패 시 {}."""
    try:
        with open(path, "r", errors="replace") as f:
            raw = f.read()
        no_comments = "\n".join(
            "" if re.match(r"\s*//", ln) else ln for ln in raw.splitlines()
        )
        no_trailing = re.sub(r",(\s*[}\]])", r"\1", no_comments)
        return json.loads(no_trailing) or {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


_pw_cache: dict[str, object] = {"mtime": None, "pw": None}


def _socket_password():
    """cmux.json(JSONC)의 automation.socketPassword 를 읽는다(mtime 캐시)."""
    try:
        mtime = os.path.getmtime(CMUX_CONFIG_JSON)
    except OSError:
        return None
    if _pw_cache["mtime"] == mtime:
        return _pw_cache["pw"]
    pw = (_load_jsonc(CMUX_CONFIG_JSON).get("automation") or {}).get("socketPassword") or None
    _pw_cache["mtime"] = mtime
    _pw_cache["pw"] = pw
    return pw


# ---- 소켓 비밀번호 self-heal (cmux 업데이트가 cmux.json 리셋 시 자동복구) ----

def stash_good_password():
    """현재 cmux.json 에 정상 비번이 있으면 대시보드 스태시에 보관(known-good).

    cmux 자동백업(cmux.<TS>.bak)은 회전돼 사라질 수 있으므로, 대시보드가 마지막
    정상 비번을 독립적으로 들고 있게 해 self_heal_password() 의 최우선 복구원으로 쓴다.
    """
    pw = (_load_jsonc(CMUX_CONFIG_JSON).get("automation") or {}).get("socketPassword")
    if not pw:
        return False
    try:
        cur = _load_jsonc(CMUX_PW_STASH).get("socketPassword")
        if cur == pw:
            return False   # 이미 최신
        os.makedirs(os.path.dirname(CMUX_PW_STASH), exist_ok=True)
        with open(CMUX_PW_STASH, "w") as f:
            json.dump({"socketPassword": pw, "savedAt": time.time()}, f)
        return True
    except OSError:
        return False


def _recover_password_candidates():
    """비번 복구원 후보를 우선순위대로 (출처, 비번) 로 yield.

    ① 대시보드 스태시(가장 신뢰) → ② cmux 자동백업 cmux.*.bak(최신순).
    """
    stash_pw = _load_jsonc(CMUX_PW_STASH).get("socketPassword")
    if stash_pw:
        yield ("stash", stash_pw)
    baks = glob.glob(os.path.join(CMUX_CONFIG_DIR, "cmux.*.bak"))
    for p in sorted(baks, key=lambda x: os.path.getmtime(x), reverse=True):
        pw = (_load_jsonc(p).get("automation") or {}).get("socketPassword")
        if pw:
            yield (os.path.basename(p), pw)


def self_heal_password():
    """cmux.json 이 'password 모드인데 비번 없음'이면 복구원에서 비번을 되써넣는다.

    반환: (healed: bool, msg: str). cmux 는 cmux.json 을 파일감시 핫리로드하므로
    되써넣기만 하면 앱 재시작 없이 서버가 비번을 반영한다.
    """
    data = _load_jsonc(CMUX_CONFIG_JSON)
    auto = data.get("automation") or {}
    mode = auto.get("socketControlMode")
    if mode != "password" or auto.get("socketPassword"):
        return False, "정상(복구 불필요)"      # 비번 있거나 password 모드 아님
    for src, pw in _recover_password_candidates():
        try:
            data.setdefault("automation", {})["socketPassword"] = pw
            data["automation"]["socketControlMode"] = "password"
            with open(CMUX_CONFIG_JSON, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            _pw_cache["mtime"] = None            # 캐시 무효화
            return True, f"복구됨(출처: {src}, 앞4자 {pw[:4]}…)"
        except OSError as e:
            return False, f"쓰기 실패: {e}"
    return False, "복구원 없음(스태시/자동백업에 비번 없음)"


def _resolve_socket():
    """현재 cmux 소켓 경로를 '실제 존재하는' 후보 중에서 고른다.

    cmux 0.64.17에서 소켓이 ~/Library/Application Support/cmux/cmux.sock →
    ~/.local/state/cmux/cmux.sock 로 이동했는데, last-socket-path 파일은 여전히
    낡은 경로를 가리킨다. 낡은(=없는) 경로를 CMUX_SOCKET_PATH 로 강제 지정하면
    'Socket not found'가 나므로, 후보를 존재검사해서 첫 번째 실존 경로를 쓴다.
    아무것도 없으면 None → 호출부에서 CMUX_SOCKET_PATH 를 아예 안 넘겨
    cmux CLI 의 기본 자동탐색에 맡긴다.
    """
    candidates = []
    env = os.environ.get("CMUX_SOCKET_PATH")
    if env:
        candidates.append(env)
    try:
        with open(CMUX_LAST_SOCKET, "r") as f:
            p = f.read().strip()
            if p:
                candidates.append(p)
    except OSError:
        pass
    candidates.append(CMUX_SOCKET_XDG)    # 신 경로(0.64.17~)
    candidates.append(CMUX_SOCKET_FILE)   # 구 경로(≤0.64.7)
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


# cmux CLI에 넘길 최소 env 화이트리스트. GHOSTTY_*/TERM_PROGRAM/__CF*/CMUX_* 등
# 터미널·cmux 식별 컨텍스트를 물려주면 cmux가 이 프로세스를 '내부 서피스'로 오판정해
# password 경로 대신 신원검사로 넘어가 거부한다(pm2는 그런 컨텍스트를 상속). 그래서
# 최소 변수만 전달하고 소켓 경로/비밀번호를 명시한다(성공한 launchd 최소-env와 동일).
_KEEP_ENV = (
    "HOME", "PATH", "USER", "LOGNAME", "TMPDIR",
    "LANG", "LC_CTYPE", "LC_ALL", "SHELL", "TERMINFO",
)


def _clean_env():
    env = {k: os.environ[k] for k in _KEEP_ENV if k in os.environ}
    sock = _resolve_socket()
    if sock:                       # 실존 소켓만 지정. 없으면 cmux 자동탐색에 맡김.
        env["CMUX_SOCKET_PATH"] = sock
    pw = _socket_password()
    if pw:
        env["CMUX_SOCKET_PASSWORD"] = str(pw)
    return env


def _base_args():
    """모든 호출 앞에 붙는 글로벌 옵션: --password 를 최우선으로 명시 전달."""
    pw = _socket_password()
    return ["--password", pw] if pw else []


# cmux 소켓은 사용자 GUI(Aqua) 세션의 프로세스만 허용한다. pm2 데몬은 다른 세션에
# 있어 직접 호출 시 'Broken pipe'로 거부된다. `launchctl asuser <uid>` 로 감싸면
# 호출이 사용자 Aqua 세션에 주입돼 cmux가 정상 수락한다(비-cmux 세션 우회의 핵심).
_USE_ASUSER = os.environ.get("CMUX_DASH_ASUSER", "1") != "0"
_UID = str(os.getuid())


def _wrap(argv):
    if _USE_ASUSER:
        return ["/bin/launchctl", "asuser", _UID, *argv]
    return argv


def _run(args, timeout=15, input_text=None):
    try:
        proc = subprocess.run(
            _wrap([CMUX_BIN, *_base_args(), *args]),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            env=_clean_env(),
        )
    except FileNotFoundError as e:
        raise CmuxError(f"cmux 바이너리를 찾을 수 없음: {CMUX_BIN}") from e
    except subprocess.TimeoutExpired as e:
        raise CmuxError(f"cmux {' '.join(args)} 타임아웃({timeout}s)") from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise CmuxError(f"cmux {' '.join(args)} 실패(rc={proc.returncode}): {msg}")
    return proc.stdout


def rpc(method, params=None, timeout=15):
    args = ["rpc", method]
    if params is not None:
        args.append(json.dumps(params))
    out = _run(args, timeout=timeout).strip()
    if not out:
        return None
    return json.loads(out)


def ping():
    try:
        _run(["ping"], timeout=5)
        return True
    except CmuxError:
        return False


def system_tree():
    """전체 라이브 구조 (windows > workspaces > panes > surfaces).

    주의: `rpc system.tree` 는 caller의 창 하나만 반환하고(pm2 detached에선 부정확),
    UUID를 얻으려면 --id-format both 가 필요하다. 따라서 caller 무관하게 전체 창 +
    UUID+ref 를 주는 `cmux --id-format both tree --all --json` 을 사용한다.
    """
    out = _run(["--id-format", "both", "tree", "--all", "--json"], timeout=20)
    return json.loads(out)


# ---- 안전 제어 액션 ----

def restore_session():
    """cmux 내장: 직전 저장 세션 전체 복원."""
    return _run(["restore-session"], timeout=40)


def open_path(path):
    """디렉토리/파일/URL 열기(디렉토리는 새 워크스페이스)."""
    return _run(["open", path], timeout=20)


def new_workspace(name=None, cwd=None, command=None, description=None,
                  window=None, layout=None, focus=False,
                  group=None, group_placement=None):
    """새 워크스페이스 생성. 원래 워크스페이스를 충실히 재구성하는 데 사용.

    - layout(JSON): 여러 탭(터미널+브라우저)을 한 워크스페이스에 재구성(--command 무시됨).
    - window: 원래 소속 창(id|ref|index)에 배치.
    반환: (stdout, 새 워크스페이스 ref 문자열 또는 None)
    """
    args = ["new-workspace", "--focus", "true" if focus else "false"]
    if name:
        args += ["--name", name[:60]]
    if description:
        args += ["--description", description[:200]]
    if window:
        args += ["--window", str(window)]
    if cwd:
        args += ["--cwd", cwd]           # 워크스페이스 기본 cwd (layout과 함께 사용 가능)
    if group:
        args += ["--group", str(group)]  # 기존 워크스페이스 그룹에 배정
        if group_placement:
            args += ["--group-placement", group_placement]
    if layout:
        args += ["--layout", layout]
    elif command:
        args += ["--command", command]
    out = _run(args, timeout=30)
    m = re.search(r"workspace:\d+", out)
    return out, (m.group(0) if m else None)


# ---- 워크스페이스 그룹(cmux 0.64.17+) RPC 래퍼 ----

def group_create(cwd=None):
    """현재(focus된) 워크스페이스를 anchor로 새 그룹 생성 → group id(UUID) 또는 None.

    cwd: 그룹 앵커(anchor) 워크스페이스의 작업 디렉토리를 명시적으로 고정한다.
         지정하지 않으면 cmux 가 첫 자식(=복원된 프로젝트 워크스페이스)의 cwd 를 앵커 cwd 로
         '추론'하는데, 그러면 그 그룹의 `+` 버튼(=그룹 내 새 워크스페이스)이 항상 그 프로젝트
         폴더에서 열린다(cmux 하드코딩: 그룹 멤버는 앵커의 currentDirectory 를 상속). 복원 그룹에는
         홈(~) 을 넘겨 앵커를 중립(홈)으로 두면 `+` 로 여는 새 워크스페이스가 홈에서 시작한다.
         (멤버 워크스페이스는 각자 --cwd 로 만들어지므로 이 값에 영향받지 않는다.)
    """
    params = {}
    if cwd:
        params["cwd"] = cwd
    r = rpc("workspace.group.create", params)
    g = (r or {}).get("group") or {}
    return g.get("id")


def group_rename(gid, name):
    return rpc("workspace.group.rename", {"group_id": gid, "name": (name or "")[:80]})


def group_pin(gid):
    """그룹 고정.

    ⚠️ **토글이 아니다.** 이미 고정된 그룹에 다시 불러도 고정인 채로 남는다(실측 2026-09-02:
       두 번 불러도 is_pinned=True). 해제는 `group_unpin` 이 따로 있다.
       덕분에 워크스페이스 pin 처럼 '원하는 최종 상태'를 그대로 지정할 수 있다 —
       토글이었다면 화면이 든 상태가 낡았을 때 정확히 반대로 뒤집혔을 것이다.
    """
    return rpc("workspace.group.pin", {"group_id": gid})


def group_unpin(gid):
    return rpc("workspace.group.unpin", {"group_id": gid})


def group_move(gid, before_gid):
    """그룹을 `before_gid` 그룹 **앞**으로 옮긴다(앵커 워크스페이스와 멤버가 통째로 따라간다).

    실측(2026-09-02)으로 확인한 것들 —
      · cmux 사이드바에서 그룹의 자리는 **앵커 워크스페이스("Group N")의 인덱스**다.
        이 RPC 를 부르면 앵커와 멤버가 함께 그 자리로 옮겨진다(교육 그룹 13장이 같이 이동).
      · `after_group_id` 도 파라미터로 받아 주지만 **실제로는 움직이지 않는다.** OK 만 돌아온다.
        그래서 뒤로 보내는 건 "뒤에 있던 그룹을 이 그룹 앞으로" 로 뒤집어서 처리해야 한다.
      · **고정(pinned)된 그룹은 움직이지 않는다.** 역시 OK 만 돌아온다.
      · 반영이 비동기라 직후에 트리를 다시 읽으면 옛 값이 나온다 — 확인하려면 잠깐 기다린다.
    """
    return rpc("workspace.group.move", {"group_id": gid, "before_group_id": before_gid})


def group_layout(window_id=None):
    """창별 그룹 배치 = [(앵커 인덱스, group_id, 이름, 고정)] — 사이드바에서 보이는 그 순서.

    `group.list` 가 주는 배열 순서는 **사이드바 순서가 아니다**(실측: 순서를 바꿔도 그대로였다).
    자리는 앵커 워크스페이스의 인덱스로만 알 수 있다.
    """
    out = {}
    for w in system_tree().get("windows", []):
        if window_id and str(w.get("id")) != str(window_id):
            continue
        try:
            r = group_list(w.get("id")) or {}
        except CmuxError:
            continue
        idx = {str(ws.get("id", "")).upper(): i for i, ws in enumerate(w.get("workspaces") or [])}
        rows = [(idx.get(str(g.get("anchor_workspace_id", "")).upper(), 10**6),
                 g.get("id"), g.get("name"), bool(g.get("is_pinned")))
                for g in (r.get("groups") or [])]
        out[r.get("window_ref") or w.get("ref")] = sorted(rows)
    return out


def group_pinned_now(gid):
    """지금 cmux 가 그렇게 여기고 있는 고정 상태(라이브). 못 찾으면 None.

    ⚠️ 그룹 상태는 네이티브 세션 JSON 에도 있지만 그쪽은 **디바운스**라 방금 한 변경이 안 보인다
       (nav.workspace_group_map 이 그 파일을 mtime 캐시로 읽는다). 확인은 반드시 이 RPC 로 한다.
    """
    want = str(gid or "").upper()
    for w in system_tree().get("windows", []):
        try:
            r = group_list(w.get("id"))
        except CmuxError:
            continue
        for g in (r or {}).get("groups") or []:
            if str(g.get("id", "")).upper() == want:
                return bool(g.get("is_pinned"))
    return None


def group_collapse(gid):
    return rpc("workspace.group.collapse", {"group_id": gid})


def group_set_icon(gid, symbol):
    return rpc("workspace.group.set_icon", {"group_id": gid, "symbol": symbol})


def group_set_color(gid, color):
    # color 형식(이름/hex)은 버전별 상이 → 실패해도 무시(best-effort)
    return rpc("workspace.group.set_color", {"group_id": gid, "color": color})


def group_add(gid, workspace_id):
    return rpc("workspace.group.add", {"group_id": gid, "workspace_id": workspace_id})


# ---- 충실 복원용 래퍼 ----

def new_window():
    """새 창 생성 → (stdout, 창 ref 또는 UUID). 출력은 'OK <uuid>' 형식."""
    out = _run(["new-window"], timeout=15)
    m = re.search(r"window:\d+", out)
    if m:
        return out, m.group(0)
    m = re.search(r"([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})", out)
    return out, (m.group(1) if m else None)


def close_window(window_ref):
    return _run(["close-window", "--window", str(window_ref)], timeout=15)


def close_workspace(ws_ref):
    return _run(["close-workspace", "--workspace", str(ws_ref)], timeout=15)


def list_windows():
    """현재 창 ref 목록(['window:1', ...])."""
    out = _run(["list-windows"], timeout=10)
    return re.findall(r"window:\d+", out)


def current_window_id():
    """현재 활성(전면) 창의 id/ref — '현재 창에 복원'용. 없으면 None."""
    try:
        t = system_tree()
    except CmuxError:
        return None
    act = t.get("active") or {}
    wid = act.get("window_id") or act.get("window_ref")
    if wid:
        return wid
    for w in t.get("windows", []):
        if w.get("current") or w.get("active"):
            return w.get("id") or w.get("ref")
    wins = t.get("windows", [])
    return (wins[0].get("id") or wins[0].get("ref")) if wins else None


def workspace_tree(ws_ref):
    """특정 워크스페이스의 라이브 구조(panes>surfaces) JSON."""
    out = _run(["--id-format", "both", "tree", "--workspace", str(ws_ref), "--json"],
               timeout=15)
    return json.loads(out)


def select_workspace(ws_ref):
    return _run(["select-workspace", "--workspace", str(ws_ref)], timeout=10)


def surface_resume_set(surface, shell=None, argv=None, cwd=None,
                       kind="agent", checkpoint=None, name=None):
    """터미널 서피스에 재개(restart) 명령을 바인딩만 한다(자동 실행 아님).

    claude resume는 `cd ... && ...` 셸 명령이므로 --shell 사용.
    """
    args = ["surface", "resume", "set", "--surface", str(surface)]
    if cwd:
        args += ["--cwd", cwd]
    if kind:
        args += ["--kind", kind]
    if checkpoint:
        args += ["--checkpoint", checkpoint]
    if name:
        args += ["--name", name[:60]]
    if shell is not None:
        args += ["--shell", shell]
    elif argv:
        args += ["--", *argv]
    return _run(args, timeout=12)


def send_text(surface_id, text):
    return rpc("surface.send_text", {"surface_id": surface_id, "text": text})


def send_key(surface_id, key):
    return rpc("surface.send_key", {"surface_id": surface_id, "key": key})


def running_claude_sids():
    """현재 실행 중인 `claude --resume <sid>` 세션 UUID 집합(ps 1회). autorun 이중기동 가드용.

    ⚠️ **`text=True` 금지** — ps 출력에는 다른 프로세스 인자 탓에 비-UTF8 바이트가 섞일 수 있고
       (실측: 0xa8), 그러면 subprocess 가 UnicodeDecodeError 를 던진다. 그걸 삼키고 빈 집합을
       반환하면 **"실행중인 세션 없음"으로 오판 → 이미 살아있는 세션을 다시 --resume(이중기동)**
       하는 사고가 난다(같은 .jsonl 에 claude 2개). 2026-07-24 실측으로 확인된 실제 버그.
       → bytes 로 받아 errors="replace" 로 디코딩(인코딩 때문에 실패할 수 없게).
    """
    try:
        raw = subprocess.run(["ps", "-axo", "command"],
                             capture_output=True, timeout=8).stdout or b""
    except Exception as e:   # noqa: BLE001 — ps 자체 실패(극히 드묾)
        # 안전 가드가 조용히 사라지지 않도록 최소한 흔적을 남긴다.
        print(f"[cmux_client] ps 실패로 실행중 세션 확인 불가: {e}", file=sys.stderr)
        return set()
    out = raw.decode("utf-8", "replace")
    return {m.group(1).lower()
            for m in re.finditer(r"--resume\s+([0-9a-fA-F-]{36})", out)}


def run_in_surface(surface_id, command, settle=0.25):
    """기존 터미널 서피스에 명령을 입력하고 실행(Enter).

    ⚠️ 반드시 '유휴 셸' 서피스에만 사용할 것 — claude 등 TUI가 떠 있는 서피스에 쓰면
       명령이 그 입력창에 텍스트로 박혀 미실행 상태로 남는다(app._is_idle_terminal 가드).
    settle: send_text 가 (특히 갓 생성된 셸에서) 처리될 시간을 준 뒤 Enter 를 보내
            'Enter 가 텍스트보다 먼저 도착해 미실행'되는 레이스를 막는다.
    """
    send_text(surface_id, command)
    if settle:
        time.sleep(settle)
    return send_key(surface_id, "Enter")


def new_surface(surface_type="terminal", workspace=None, url=None, focus=False):
    """기존 워크스페이스에 서피스(탭) 추가. 반환: (stdout, id_또는_ref)."""
    args = ["--id-format", "both", "new-surface", "--type", surface_type,
            "--focus", "true" if focus else "false"]
    if workspace:
        args += ["--workspace", str(workspace)]
    if url:
        args += ["--url", url]
    out = _run(args, timeout=15)
    mid = re.search(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", out)
    mref = re.search(r"surface:\d+", out)
    return out, (mid.group(0) if mid else (mref.group(0) if mref else None))


def workspace_action(workspace, action, color=None):
    """워크스페이스 컨텍스트 액션(pin/unpin/set-color 등)."""
    args = ["workspace-action", "--workspace", str(workspace), "--action", action]
    if color:
        args += ["--color", color]
    return _run(args, timeout=10)


def focus_surface(surface_id):
    """서피스 포커스 → 해당 워크스페이스로 전환."""
    return rpc("surface.focus", {"surface_id": surface_id})


def focused_surface():
    """지금 포커스된 surface {id, ref, window_ref} — 포커스 이동 결과 검증용.

    `system_tree()`(전체 트리, 실측 405ms)보다 가볍다(164ms). 검증에는 활성 서피스 하나면
    충분하므로 트리를 통째로 받지 않는다.
    """
    out = _run(["--id-format", "both", "identify", "--json"], timeout=10)
    f = (json.loads(out) or {}).get("focused") or {}
    return {"id": f.get("surface_id"), "ref": f.get("surface_ref"),
            "window_ref": f.get("window_ref")}


def activate_app():
    """cmux.app 을 macOS **최전면**으로 가져온다.

    `surface.focus` 는 cmux **내부**에서만 창/워크스페이스/탭을 바꾼다. 브라우저 등 다른 앱을
    보고 있으면 cmux 는 뒤에 남아 "이동했다"는 말만 뜨고 화면은 그대로다 — 사용자가 직접
    cmux 를 찾아야 했다. macOS 레벨 활성화는 소켓이 아니라 `open -a` 로 한다.
    (pm2 프로세스도 사용자 Aqua 세션 소속이라 동작한다. 실패 시 osascript 로 폴백.)
    """
    from config import CMUX_APP, CMUX_BUNDLE_ID
    try:
        r = subprocess.run(["open", "-a", CMUX_APP], capture_output=True, timeout=8)
        if r.returncode == 0:
            return True, "open -a"
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
    except Exception as e:                                    # noqa: BLE001
        err = str(e)
    try:
        r = subprocess.run(
            ["osascript", "-e", f'tell application id "{CMUX_BUNDLE_ID}" to activate'],
            capture_output=True, timeout=8)
        if r.returncode == 0:
            return True, "osascript"
        err += " / " + (r.stderr or b"").decode("utf-8", "replace").strip()
    except Exception as e:                                    # noqa: BLE001
        err += f" / {e}"
    print(f"[cmux_client] 앱 활성화 실패: {err}", file=sys.stderr)
    return False, err


def workspace_list(window_id):
    """한 창의 워크스페이스를 **사이드바 순서 그대로** 준다(라이브).

    tree 로도 같은 순서를 얻지만 이쪽이 `pinned`·`index` 를 명시적으로 주고 창 하나만
    물어볼 수 있어 편집 모드의 정본으로 쓴다.
    """
    return rpc("workspace.list", {"window_id": window_id})


def group_list(window_id):
    """한 창의 워크스페이스 그룹 정의 + **라이브 멤버십**을 준다.

    ★ 이게 중요한 이유 — 그룹 멤버십을 디스크의 네이티브 세션 JSON 에서 읽으면
      디바운스 때문에 방금 한 재배치가 안 보인다. `member_workspace_ids` 는 지금
      cmux 가 실제로 그렇게 여기고 있는 값이라, "옆 그룹이 멤버를 흡수했는지"를
      추측이 아니라 대조로 판정할 수 있다(2026-07-22 사고의 재발 방지).
    """
    return rpc("workspace.group.list", {"window_id": window_id})


def reorder_workspace(workspace_id, index=None, before=None, after=None,
                      window_id=None, dry_run=False):
    """한 창 안에서 워크스페이스 위치를 옮긴다. `--dry-run` 으로 계획만 볼 수도 있다."""
    args = ["reorder-workspace", "--workspace", workspace_id]
    if index is not None:
        args += ["--index", str(index)]
    elif before:
        args += ["--before", before]
    elif after:
        args += ["--after", after]
    if window_id:
        args += ["--window", window_id]
    if dry_run:
        args += ["--dry-run"]
    return _run(args, timeout=15)


# `OK plan workspace=workspace:112 window=window:13 index=5` 를 뜯는다.
_PLAN_RE = re.compile(r"workspace=(\S+)\s+window=(\S+)\s+index=(\d+)")


def parse_reorder_plan(raw):
    """`OK plan workspace=workspace:112 window=window:13 index=5` 를 뜯는다.

    ⚠️ cmux 는 요청한 인덱스를 **조용히 클램프한다.** 고정(pinned)된 워크스페이스는
       고정 블록 밖으로 못 나가고, 고정 아닌 워크스페이스는 그 안으로 못 들어온다.
       그런데도 종료코드는 0 이고 출력은 `OK plan ...` 이라, 반환값만 보면 요청대로
       된 줄 안다(실측: `--index 8` 요청이 index=5 로, `--index 0` 요청이 index=6 으로).
       → 계획을 파싱해 원하는 위치와 대조해야 어긋남을 드러낼 수 있다.
       [[feedback_no_silent_zero]]
    """
    return {m.group(1): int(m.group(3)) for m in _PLAN_RE.finditer(raw or "")}


def reorder_workspaces(order, window_id=None, dry_run=False):
    """창 하나의 워크스페이스 순서를 한 번에 지정한다.

    ⚠️ **편집 모드는 이 함수를 쓰지 않는다.** 지금 순서를 그대로 넣어도 cmux 가
       항등으로 계획하지 않기 때문이다 — 실측(창 2, 고정 5개): 현재 배치를 그대로
       `--order` 로 넣으면 그룹 앵커를 index 0 → 5 로 옮기는 계획이 나온다. 한 장만
       옮기려던 드래그가 앵커까지 건드리게 된다. 그래서 편집 모드는 **바뀐 것만
       한 장씩** 옮긴다(app.py `_plan_moves`). 이 함수는 전량 재정렬이 실제로 필요한
       경우를 위해 남겨 둔다.
    """
    args = ["reorder-workspaces", "--order", ",".join(order)]
    if window_id:
        args += ["--window", window_id]
    if dry_run:
        args += ["--dry-run"]
    raw = _run(args, timeout=30)
    return {"raw": raw, "plan": parse_reorder_plan(raw)}


def move_workspace_to_window(workspace_id, window_id):
    """워크스페이스를 다른 창으로 옮긴다."""
    return _run(["move-workspace-to-window", "--workspace", workspace_id,
                 "--window", window_id], timeout=20)


def group_add_workspace(group_id, workspace_id):
    """워크스페이스를 그룹에 넣는다.

    ⚠️ cmux 그룹 멤버십은 groupId 가 아니라 사이드바의 **연속 위치**로 결정된다.
       그래서 순서를 옮긴 뒤에는 반드시 이 호출로 소속을 다시 못 박아야 옆 그룹에
       흡수되지 않는다(2026-07-22 실측 사고).
    """
    return rpc("workspace.group.add", {"group_id": group_id, "workspace_id": workspace_id})


def group_remove_workspace(workspace_id):
    """워크스페이스를 지금 속한 그룹에서 뺀다.

    그룹 밖으로 끌어낸 카드를 '위치가 알아서 정리해 주겠지'로 두면 안 된다 —
    멤버십이 연속 위치로 계산되는 탓에 경계에 놓인 워크스페이스는 옆 그룹에 남거나
    흡수된다. 뺄 때도 명시적으로 뺀다.
    """
    return rpc("workspace.group.remove", {"workspace_id": workspace_id})


def focus_window(window_id):
    return _run(["focus-window", "--window", window_id], timeout=10)


# ---- 이벤트 스트림 ----

def events_popen(after=None, cursor_file=None):
    """`cmux events` 프로세스를 띄우고 Popen 핸들 반환(라인 단위 JSON stdout)."""
    args = _wrap([CMUX_BIN, *_base_args(), "events", "--no-heartbeat"])
    if after is not None:
        args += ["--after", str(after)]
    if cursor_file:
        args += ["--cursor-file", cursor_file]
    return subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1, env=_clean_env(),
    )

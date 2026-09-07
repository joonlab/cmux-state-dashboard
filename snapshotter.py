"""스냅샷터: cmux 라이브 상태를 정규화·해시·저장하고 복구 파일을 기록한다.

두 축을 결합한다(각각 독립적으로 견고):
  1) 복구 세션(sessions): cmux 네이티브 세션 json의 pane별 `--resume <sid>` 명령에서 추출.
     → cmux 재시작/강제종료에도 보존되는 '무엇을 어떻게 재개하나'의 원천. .jsonl aiTitle로 라벨.
  2) 라이브 구조(windows): `cmux --id-format both tree --all --json` (전체 창/워크스페이스/서피스).
     → 각 워크스페이스의 claude는 실행중 프로세스(tty) 또는 제목(=aiTitle) 매칭으로 best-effort 표시.

주의: claude-hook-sessions.json 의 workspace UUID 는 cmux 재시작 시 stale 해지므로 신뢰하지 않는다.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time

import cmux_client
import claude_index
import db
import nav
import layout
from config import (
    NATIVE_JSON, BACKUP_DIR, DATA_DIR, RECOVERY_JSON, RECOVERY_TXT,
    POLL_INTERVAL, EVENT_DEBOUNCE, RETENTION_DAYS, MAX_NATIVE_BACKUPS,
    RETAIN_FULL_DAYS, RETAIN_HOURLY_DAYS, RETAIN_COARSE_BUCKET_SEC,
    PRUNE_INTERVAL_SEC,
)

_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
_CD = re.compile(r"cd '([^']+)'")
_PS_SID = re.compile(r"--(?:session-id|resume)\s+'?([0-9a-fA-F-]{36})")

_LIFECYCLE_HINTS = (
    "sessionstart", "sessionend", "stop", "created", "closed", "opened",
    "removed", "renamed", "workspace", "window",
)


def _load_json(path):
    try:
        with open(path, "r", errors="replace") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# ---------- 소스 1: 네이티브 json resume 바인딩 ----------
def _native_rows(native):
    """네이티브 세션 json → [(windowIndex, title, sid, cwd, command)]. sid 없는 워크스페이스도 포함."""
    rows = []
    if not native:
        return rows

    def find_cmd(node):
        if isinstance(node, dict):
            c = node.get("command")
            if isinstance(c, str) and "resume" in c and _UUID.search(c):
                return c
            for v in node.values():
                r = find_cmd(v)
                if r:
                    return r
        elif isinstance(node, list):
            for i in node:
                r = find_cmd(i)
                if r:
                    return r
        return None

    def collect(node, win):
        if isinstance(node, dict):
            if isinstance(node.get("workspaces"), list):
                for ws in node["workspaces"]:
                    title = ws.get("name") or ws.get("customTitle") or ws.get("title")
                    cmd = find_cmd(ws)
                    m = _UUID.search(cmd) if cmd else None
                    sid = m.group(1) if m else None
                    cwd = None
                    if cmd:
                        m = _CD.search(cmd)
                        cwd = m.group(1) if m else None
                    rows.append((win, title, sid, cwd, cmd.strip() if cmd else None))
            for v in node.values():
                collect(v, win)
        elif isinstance(node, list):
            for i in node:
                collect(i, win)

    for wi, win in enumerate(native.get("windows", [])):
        collect(win, wi)
    return rows


# ---------- 소스 2: 실행중 claude 프로세스 ----------
def _running_sids():
    """ps → (실행중 sid 집합, {정규화tty: sid})."""
    sids, by_tty = set(), {}
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,tty,command"],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except Exception:
        return sids, by_tty
    for line in out.splitlines():
        if "claude" not in line:
            continue
        if not (".local/bin/claude" in line or "claude-resume" in line
                or "/claude " in line or line.rstrip().endswith("/claude")):
            continue
        m = _PS_SID.search(line)
        if not m:
            continue
        sid = m.group(1).lower()
        sids.add(sid)
        parts = line.split(None, 2)
        if len(parts) >= 2:
            tty = parts[1]
            if tty not in ("??", "?", "-"):
                by_tty[tty.replace("tty", "")] = sid
    return sids, by_tty


def _norm_tty(t):
    return t.replace("tty", "") if t else t


# ---------- 정규화 ----------
def _build_sessions(native_rows, running_sids):
    """native_rows → {sid: 세션정보} + 제목 매칭용 인덱스."""
    sessions, aititle_to_sid, ntitle_to_sid = {}, {}, {}
    for (wi, title, sid, cwd, cmd) in native_rows:
        if not sid:
            continue
        sid = sid.lower()
        meta = claude_index.session_meta(sid)
        label = meta.get("label") if meta.get("exists") else (title or "(제목 없음)")
        info = {
            "sessionId": sid, "label": label, "nativeTitle": title,
            "cwd": cwd or meta.get("cwd"), "resumeCommand": cmd,
            "plainResume": f"claude --resume {sid}",
            "lastActivity": meta.get("lastActivity"), "msgCount": meta.get("msgCount"),
            "exists": meta.get("exists"), "projectDir": meta.get("projectDir"),
            "windowIndex": wi, "running": sid in running_sids,
        }
        prev = sessions.get(sid)
        if not prev or (info["lastActivity"] or 0) >= (prev["lastActivity"] or 0):
            sessions[sid] = info
        if meta.get("exists") and meta.get("label"):
            aititle_to_sid.setdefault(meta["label"], sid)
        if title:
            ntitle_to_sid.setdefault(title, sid)

    # 실행중이지만 네이티브 바인딩이 없는 세션도 포함한다.
    # cmux 가 창을 세션 JSON 에 기록하지 않으면(2026-08-04 실사고) 그 창의 claude 는
    # native_rows 에 없어 sessions 에서 빠지고, tree 쪽 tty 매칭이 성공해도
    # `match_sid in sessions` 조건에서 버려져 결국 "claude 없음"으로 보였다.
    for sid in running_sids:
        sid = sid.lower()
        if sid in sessions:
            continue
        meta = claude_index.session_meta(sid)
        cmd, cwd = claude_index.build_resume(sid, meta.get("cwd"))
        sessions[sid] = {
            "sessionId": sid, "label": meta.get("label") or "(제목 없음)",
            "nativeTitle": None, "cwd": cwd or meta.get("cwd"),
            "resumeCommand": cmd, "plainResume": f"claude --resume {sid}",
            "lastActivity": meta.get("lastActivity"), "msgCount": meta.get("msgCount"),
            "exists": meta.get("exists"), "projectDir": meta.get("projectDir"),
            "windowIndex": None, "running": True, "nativeBound": False,
        }
        if meta.get("exists") and meta.get("label"):
            aititle_to_sid.setdefault(meta["label"], sid)
    return sessions, aititle_to_sid, ntitle_to_sid


def _claude_view(s):
    return {k: s[k] for k in (
        "sessionId", "label", "cwd", "resumeCommand", "plainResume",
        "lastActivity", "msgCount", "exists", "projectDir", "running")}


def _windows_from_native(native_rows, sessions):
    """소켓(tree) 미가용 시: 네이티브 json 구조로 windows 구성(서피스 없음)."""
    from collections import OrderedDict
    wins = OrderedDict()
    for (wi, title, sid, cwd, cmd) in native_rows:
        claude = None
        if sid and sid.lower() in sessions:
            claude = _claude_view(sessions[sid.lower()])
        wins.setdefault(wi, []).append({
            "id": None, "ref": None, "title": title or "(무제)",
            "selected": None, "pinned": None, "claude": claude, "panes": [],
        })
    out = []
    for wi, wss in wins.items():
        out.append({"id": None, "ref": None, "index": wi, "title": f"창 {wi + 1}",
                    "selectedWorkspaceId": None, "workspaces": wss})
    return out


def build_normalized(tree, native):
    native_rows = _native_rows(native)
    running_sids, tty_sid = _running_sids()
    # ★ surface UUID → 세션 UUID **정확** 매핑. 제목 매칭보다 항상 우선한다.
    #   제목만으로 붙이면 **같은 제목의 워크스페이스가 둘일 때 한 세션이 양쪽에 붙는다**
    #   (2026-08-25 실측: 창3·창7 의 "이미지 #2 #3" 두 곳에 같은 세션이 표시되어, 사용자가
    #    엉뚱한 쪽을 정답으로 오인했다). cmux 가 claude 에 심는 CMUX_PANEL_ID 를 쓰면 확정된다.
    try:
        _procs, _ = nav.claude_processes()
        panel_sid = {p["surfaceId"]: p["sessionId"]
                     for p in _procs if p.get("surfaceId") and p.get("sessionId")}
    except Exception as e:                                    # noqa: BLE001
        print(f"[snapshotter] surface→세션 매핑 실패(제목 매칭으로 폴백): {e}", flush=True)
        panel_sid = {}
    bound_sids = set(panel_sid.values())
    sessions, aititle_to_sid, ntitle_to_sid = _build_sessions(native_rows, running_sids)

    if not tree:
        # 소켓 미가용 → 네이티브 json 구조 사용(라이브 tree 대체)
        windows_out = _windows_from_native(native_rows, sessions)
        sessions_list = sorted(sessions.values(),
                               key=lambda s: s.get("lastActivity") or 0, reverse=True)
        norm = {
            "capturedAt": time.time(), "windows": windows_out,
            "sessions": sessions_list, "source": "native",
            "runningCount": len(running_sids & set(sessions)),
            "stats": {"windows": len(windows_out),
                      "workspaces": sum(len(w["workspaces"]) for w in windows_out),
                      "sessions": len(sessions_list)},
        }
    else:
        norm = _build_from_tree(
            tree, sessions, aititle_to_sid, ntitle_to_sid, running_sids, tty_sid,
            panel_sid, bound_sids)

    # 충실 복원용 레이아웃 트리 — 항상 네이티브 세션 JSON 이 원천(dividerPosition 등은 소켓에 없음).
    norm["layoutWindows"] = layout.parse_native_layout(native, claude_index.session_meta)
    norm["layoutStats"] = layout.layout_stats(norm["layoutWindows"])
    _annotate_source_check(norm)
    return norm


def _annotate_source_check(norm):
    """tree(라이브) ↔ native(복원 원천) 불일치를 계측해 스냅샷에 남긴다.

    2026-08-04: cmux 가 창 하나를 세션 JSON 에 기록하지 않은 채 하루가 지났고, 재부팅으로
    그 창이 통째로 소실됐다. 그동안 대시보드는 "창 2개"만 보여주며 **아무 경고도 하지 않았다**
    — 유실이 조용히 진행된 것. 이제 불일치를 수치로 남기고 UI 가 경고한다.
    합성 결과 자체는 근사값이라 저장하지 않는다(복원 원천 오염 방지). 복원 시 읽기 시점에 만든다.
    """
    ls = norm.get("layoutStats") or {}
    tree_wins = norm.get("windows") or []
    try:
        synth = layout.synthesize_tree_windows(
            tree_wins, norm.get("layoutWindows") or [], claude_index.session_meta)
    except Exception:
        synth = []
    ts = layout.layout_stats(synth) if synth else {
        "windows": 0, "workspaces": 0, "claude": 0, "terminal": 0, "browser": 0}
    norm["treeOnlyStats"] = ts
    norm["sourceCheck"] = {
        "treeWindows": len(tree_wins),
        "nativeWindows": ls.get("windows", 0),
        "treeOnlyWindows": ts["windows"],
        "treeOnlyWorkspaces": ts["workspaces"],
        "treeOnlyClaude": ts["claude"],
        "mismatch": ts["windows"] > 0,
    }
    # 목록/피커 카운트는 '복원 가능한 총량' = 네이티브 + 합성분.
    norm["restorableStats"] = {
        "windows": ls.get("windows", 0) + ts["windows"],
        "workspaces": ls.get("workspaces", 0) + ts["workspaces"],
        "claude": ls.get("claude", 0) + ts["claude"],
    }


def _build_from_tree(tree, sessions, aititle_to_sid, ntitle_to_sid, running_sids, tty_sid,
                     panel_sid=None, bound_sids=None):
    panel_sid = panel_sid or {}
    bound_sids = bound_sids or set()
    # 라이브 구조 (tree)
    windows_out = []
    for w in (tree or {}).get("windows", []):
        w_index = w.get("index", 0)
        ws_out = []
        for ws in w.get("workspaces", []):
            ws_title = ws.get("title") or "(무제)"
            panes_out = []
            match_sid = None
            term_titles = []
            for pn in ws.get("panes", []):
                surfaces_out = []
                for sf in pn.get("surfaces", []):
                    tty = sf.get("tty")
                    exact = panel_sid.get(str(sf.get("id") or "").upper())
                    if exact:
                        match_sid = exact          # 확정 — 제목 추측이 덮지 못하게 한다
                    elif tty and _norm_tty(tty) in tty_sid and not match_sid:
                        match_sid = tty_sid[_norm_tty(tty)]
                    if sf.get("type") != "browser" and sf.get("title"):
                        term_titles.append(sf["title"])
                    surfaces_out.append({
                        "id": sf.get("id"), "ref": sf.get("ref"),
                        "type": sf.get("type"), "title": sf.get("title"),
                        "url": sf.get("url"), "tty": tty,
                        "selected": sf.get("selected"),
                    })
                panes_out.append({
                    "id": pn.get("id"), "ref": pn.get("ref"),
                    "focused": pn.get("focused"), "surfaces": surfaces_out,
                })
            # 정확 매핑이 없을 때만 제목(=aiTitle) → nativeTitle 순으로 추측한다.
            # ⚠️ 이미 다른 탭에 확정된 세션은 재사용 금지 — 그게 동명 워크스페이스 중복 배정의 원인이다.
            if not match_sid:
                cand = aititle_to_sid.get(ws_title) or ntitle_to_sid.get(ws_title)
                match_sid = None if cand in bound_sids else cand
            # 그래도 실패하면 터미널 서피스 제목(=aiTitle)으로 .jsonl 역매칭.
            # 네이티브에 없는 창은 위 두 인덱스에 아예 없으므로 이 경로가 유일한 구제책이다.
            if not match_sid:
                for t in term_titles:
                    cand = claude_index.find_by_title(t)
                    if cand and cand not in bound_sids:
                        match_sid = cand
                        break
            # 세션 사전에 없으면(=네이티브 바인딩 없음) 즉석에서 만들어 넣는다.
            if match_sid and match_sid not in sessions:
                meta = claude_index.session_meta(match_sid)
                if meta.get("exists"):
                    cmd, cwd = claude_index.build_resume(match_sid, meta.get("cwd"))
                    sessions[match_sid] = {
                        "sessionId": match_sid, "label": meta.get("label") or ws_title,
                        "nativeTitle": None, "cwd": cwd or meta.get("cwd"),
                        "resumeCommand": cmd, "plainResume": f"claude --resume {match_sid}",
                        "lastActivity": meta.get("lastActivity"), "msgCount": meta.get("msgCount"),
                        "exists": True, "projectDir": meta.get("projectDir"),
                        "windowIndex": w_index, "running": match_sid in running_sids,
                        "nativeBound": False,
                    }
                else:
                    match_sid = None
            claude = None
            if match_sid and match_sid in sessions:
                s = sessions[match_sid]
                claude = {k: s[k] for k in (
                    "sessionId", "label", "cwd", "resumeCommand", "plainResume",
                    "lastActivity", "msgCount", "exists", "projectDir", "running")}
            ws_out.append({
                "id": ws.get("id"), "ref": ws.get("ref"), "title": ws_title,
                "selected": ws.get("selected"), "pinned": ws.get("pinned"),
                "description": ws.get("description"),
                "claude": claude, "panes": panes_out,
            })
        windows_out.append({
            "id": w.get("id"), "ref": w.get("ref"), "index": w_index,
            "title": f"창 {w_index + 1}",
            "selectedWorkspaceId": w.get("selected_workspace_id"),
            "workspaces": ws_out,
        })

    sessions_list = sorted(
        sessions.values(), key=lambda s: s.get("lastActivity") or 0, reverse=True
    )
    return {
        "capturedAt": time.time(),
        "windows": windows_out,
        "sessions": sessions_list,
        "source": "socket",
        "runningCount": len(running_sids & set(sessions)),
        "stats": {
            "windows": len(windows_out),
            "workspaces": sum(len(w["workspaces"]) for w in windows_out),
            "sessions": len(sessions_list),
        },
    }


def _hash_projection(norm):
    """구조 + 세션 집합만 반영(포커스/브라우저 nav 무시)."""
    parts = []
    for w in norm["windows"]:
        parts.append(f"W{w['index']}")
        for ws in w["workspaces"]:
            parts.append(f"WS:{ws.get('id')}:{ws['title']}")
            for pn in ws["panes"]:
                for sf in pn["surfaces"]:
                    if sf["type"] == "terminal":
                        parts.append(f"T:{sf.get('id')}:{sf.get('title')}")
                    else:
                        parts.append(f"S:{sf.get('id')}")
    for sid in sorted(norm["sessions"], key=lambda s: s["sessionId"]):
        parts.append(f"C:{sid['sessionId']}:{sid['running']}")
    # 레이아웃 구조(탭 구성/스플릿/색) 변화도 새 스냅샷을 트리거
    for lp in layout.layout_hash_parts(norm.get("layoutWindows") or []):
        parts.append("L:" + lp)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _write_recovery_files(norm):
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(RECOVERY_JSON, "w") as f:
            json.dump(norm, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    lines = []
    ts = dt.datetime.fromtimestamp(norm["capturedAt"]).strftime("%Y-%m-%d %H:%M:%S")
    st = norm["stats"]
    lines.append(f"# cmux 복구표  (스냅샷: {ts})")
    lines.append(f"# 창 {st['windows']} / 워크스페이스 {st['workspaces']} / "
                 f"Claude 세션 {st['sessions']} (실행중 {norm['runningCount']})")
    lines.append("# 최근 활동 순. 한 번에 다 열지 말고 4~8개씩 재개하세요(OOM 예방).\n")
    for s in norm["sessions"]:
        run = " [실행중]" if s["running"] else ""
        # 폴더 이동/rename으로 stale해진 cd 프리픽스를 실제 launch cwd로 교정(수동 복사 대비).
        cmd, cwd = claude_index.correct_resume(
            s["sessionId"], s.get("resumeCommand"), s.get("cwd"))
        lines.append(f"[{s.get('label') or '(제목 없음)'}]{run}")
        lines.append(f"  세션 {s['sessionId']}  cwd {cwd or '?'}")
        cmd = cmd or f"( cd {json.dumps(cwd or '~')} && {s['plainResume']} )"
        lines.append(f"  {cmd}\n")
    try:
        with open(RECOVERY_TXT, "w") as f:
            f.write("\n".join(lines))
    except OSError:
        pass


def _backup_native():
    if not os.path.exists(NATIVE_JSON):
        return
    try:
        with open(NATIVE_JSON, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return
    if db.get_meta("native_hash") == h:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        shutil.copy2(NATIVE_JSON, os.path.join(BACKUP_DIR, f"session_{stamp}.json"))
    except OSError:
        return
    db.set_meta("native_hash", h)
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("session_"))
    for old in files[:-MAX_NATIVE_BACKUPS]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def _prune_if_due():
    """계층적 보존 정리 — 버킷 그룹핑 쿼리가 비싸므로 PRUNE_INTERVAL_SEC 간격으로만."""
    try:
        last = float(db.get_meta("prune_ts") or 0)
    except (TypeError, ValueError):
        last = 0.0
    now = time.time()
    if now - last < PRUNE_INTERVAL_SEC:
        return None
    db.set_meta("prune_ts", now)
    try:
        return db.prune_tiered(RETAIN_FULL_DAYS, RETAIN_HOURLY_DAYS,
                               RETAIN_COARSE_BUCKET_SEC)
    except Exception as e:
        print(f"[prune] 실패: {e}", flush=True)
        return None


class Snapshotter:
    def __init__(self):
        self._lock = threading.Lock()
        self._live_cache = None
        self._live_cache_ts = 0.0
        self._pending_due = 0.0
        self._stop = threading.Event()
        self._last_milestone_day = None

    def build_live(self, use_cache=True):
        now = time.time()
        if use_cache and self._live_cache and (now - self._live_cache_ts) < 2:
            return self._live_cache
        # 소켓(tree) 가용 시 라이브 구조 사용, 실패 시 네이티브 json 폴백.
        try:
            tree = cmux_client.system_tree()
        except Exception:
            tree = None
        native = _load_json(NATIVE_JSON)
        norm = build_normalized(tree, native)
        self._live_cache = norm
        self._live_cache_ts = now
        return norm

    def capture(self, reason="poll"):
        with self._lock:
            try:
                norm = self.build_live(use_cache=False)
            except Exception as e:
                return {"ok": False, "error": str(e), "reason": reason}
            h = _hash_projection(norm)
            today = dt.date.today().isoformat()
            # 마일스톤(하루 첫 스냅샷)은 db.meta에 영속화 → pm2 재시작마다 중복 생성 방지
            is_ms = db.get_meta("milestone_day") != today
            # 피커/목록 카운트 = **복원 가능한 총량**(네이티브 레이아웃 + tree-only 합성분).
            # 2026-07-23 에는 복원 원천이 네이티브뿐이라 native 기준으로 통일했으나, 그 결과
            # 네이티브에 없는 창이 카운트에서 조용히 사라졌다(2026-08-04 하루치 유실).
            # 이제 그런 창도 합성으로 복원되므로 카운트에 포함한다.
            ls = norm.get("layoutStats") or {}
            rs = norm.get("restorableStats") or {}
            disp = {
                "windows": rs.get("windows", ls.get("windows", norm["stats"].get("windows", 0))),
                "workspaces": rs.get("workspaces", ls.get("workspaces", norm["stats"].get("workspaces", 0))),
                "sessions": rs.get("claude", ls.get("claude", norm["stats"].get("sessions", 0))),
            }
            inserted, snap_id = db.record_snapshot(h, norm, disp, is_milestone=is_ms)
            if is_ms:
                db.set_meta("milestone_day", today)
            for s in norm["sessions"]:
                db.upsert_session(s)
            _write_recovery_files(norm)
            _backup_native()
            _prune_if_due()
            return {"ok": True, "inserted": inserted, "snapshotId": snap_id,
                    "reason": reason, "stats": norm["stats"]}

    def start(self):
        threading.Thread(target=self._poll_loop, daemon=True, name="snap-poll").start()
        threading.Thread(target=self._event_loop, daemon=True, name="snap-events").start()
        threading.Thread(target=self._debounce_loop, daemon=True, name="snap-debounce").start()

    def stop(self):
        self._stop.set()

    def _poll_loop(self):
        while not self._stop.is_set():
            self.capture(reason="poll")
            self._stop.wait(POLL_INTERVAL)

    def _debounce_loop(self):
        while not self._stop.is_set():
            self._stop.wait(1)
            if self._pending_due and time.time() >= self._pending_due:
                self._pending_due = 0.0
                self.capture(reason="event")

    def _trigger(self):
        self._pending_due = time.time() + EVENT_DEBOUNCE

    def _event_loop(self):
        while not self._stop.is_set():
            # 소켓 제어 불가(cmux 미실행/외부세션)면 이벤트 구독 대신 폴링에 맡기고 길게 대기.
            if not cmux_client.ping():
                self._stop.wait(60)
                continue
            proc = None
            try:
                proc = cmux_client.events_popen()
                for line in proc.stdout:
                    if self._stop.is_set():
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    name = (ev.get("name") or "").lower()
                    cat = (ev.get("category") or "").lower()
                    if cat in ("workspace", "window", "surface") or any(
                        hnt in name for hnt in _LIFECYCLE_HINTS
                    ):
                        self._trigger()
            except Exception:
                pass
            finally:
                if proc:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            self._stop.wait(5)

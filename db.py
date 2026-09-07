"""SQLite 저장소: 스냅샷 이력 + 세션 인덱스."""
import json
import os
import sqlite3
import threading
import time
import zlib

from config import DB_PATH, DATA_DIR

_lock = threading.Lock()

# ---------- normalized_json 압축 ----------
# 스냅샷 본문은 JSON 텍스트라 압축률이 매우 높다(실측 4.4배, 1.2ms/건).
# 보존 기간을 늘리려면 필수 — 무압축은 하루 ~106MB, 압축하면 ~24MB.
# 컬럼 타입은 TEXT 이지만 SQLite 는 동적 타입이라 BLOB 을 그대로 담을 수 있다.
# 읽기는 str(구 데이터)/bytes(신 데이터) 양쪽을 모두 받는다 → 마이그레이션 없이도 동작.
_ZLEVEL = 6


def encode_normalized(normalized):
    return zlib.compress(json.dumps(normalized, ensure_ascii=False).encode("utf-8"), _ZLEVEL)


def decode_normalized(value):
    if isinstance(value, bytes):
        try:
            value = zlib.decompress(value).decode("utf-8")
        except zlib.error:
            value = value.decode("utf-8", "replace")   # 혹시 모를 비압축 BLOB
    return json.loads(value)


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_ts REAL NOT NULL,
                last_ts REAL NOT NULL,
                hash TEXT NOT NULL,
                is_milestone INTEGER DEFAULT 0,
                n_windows INTEGER, n_workspaces INTEGER, n_sessions INTEGER,
                normalized_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_snap_last ON snapshots(last_ts);
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                label TEXT, cwd TEXT, project_dir TEXT, jsonl_path TEXT,
                resume_command TEXT, last_ts REAL, msg_count INTEGER,
                first_seen REAL
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS restored (
                key TEXT PRIMARY KEY, ts REAL, label TEXT
            );
            """
        )


def restored_set():
    with _lock, _conn() as c:
        return {r["key"] for r in c.execute("SELECT key FROM restored").fetchall()}


def mark_restored(keys, labels=None):
    now = time.time()
    labels = labels or {}
    with _lock, _conn() as c:
        for k in keys:
            c.execute("INSERT OR REPLACE INTO restored(key,ts,label) VALUES(?,?,?)",
                      (k, now, labels.get(k)))


def unmark_restored(keys):
    with _lock, _conn() as c:
        c.executemany("DELETE FROM restored WHERE key=?", [(k,) for k in keys])


def clear_restored():
    with _lock, _conn() as c:
        c.execute("DELETE FROM restored")


def get_latest():
    with _lock, _conn() as c:
        r = c.execute(
            "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None


def record_snapshot(hash_, normalized, stats, is_milestone=False):
    """직전과 hash 동일하면 last_ts만 갱신, 다르면 새 행 삽입. (inserted?, snapshot_id) 반환."""
    now = time.time()
    with _lock, _conn() as c:
        latest = c.execute(
            "SELECT id, hash FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest and latest["hash"] == hash_ and not is_milestone:
            c.execute("UPDATE snapshots SET last_ts=? WHERE id=?", (now, latest["id"]))
            return False, latest["id"]
        cur = c.execute(
            """INSERT INTO snapshots
               (first_ts, last_ts, hash, is_milestone, n_windows, n_workspaces,
                n_sessions, normalized_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            (now, now, hash_, 1 if is_milestone else 0,
             stats.get("windows", 0), stats.get("workspaces", 0),
             stats.get("sessions", 0), encode_normalized(normalized)),
        )
        return True, cur.lastrowid


def list_snapshots(limit=200, offset=0, milestones_only=False):
    where = "WHERE is_milestone=1" if milestones_only else ""
    with _lock, _conn() as c:
        rows = c.execute(
            f"""SELECT id, first_ts, last_ts, hash, is_milestone,
                      n_windows, n_workspaces, n_sessions
               FROM snapshots {where} ORDER BY id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def update_snapshot_normalized(snap_id, normalized):
    """스냅샷의 normalized_json 을 통째 교체(레이아웃 백필/메모이즈용)."""
    with _lock, _conn() as c:
        c.execute("UPDATE snapshots SET normalized_json=? WHERE id=?",
                  (encode_normalized(normalized), snap_id))


def get_snapshot(snap_id):
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["normalized"] = decode_normalized(d.pop("normalized_json"))
        return d


def count_snapshots(milestones_only=False):
    where = "WHERE is_milestone=1" if milestones_only else ""
    with _lock, _conn() as c:
        return c.execute(f"SELECT COUNT(*) n FROM snapshots {where}").fetchone()["n"]


def snapshot_offset_for_date(day_start_ts, milestones_only=False):
    """주어진 시각 **이후**(= 목록에서 더 위)에 있는 스냅샷 수 = 그 날로 점프할 offset.

    목록이 id DESC(최신순)이므로, 해당 시각보다 뒤(최신)인 항목 수가 곧 건너뛸 개수다.
    """
    cond = "last_ts > ?"
    if milestones_only:
        cond += " AND is_milestone=1"
    with _lock, _conn() as c:
        return c.execute(
            f"SELECT COUNT(*) n FROM snapshots WHERE {cond}", (day_start_ts,)
        ).fetchone()["n"]


def upsert_session(sess):
    now = time.time()
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO sessions
               (session_id, label, cwd, project_dir, jsonl_path, resume_command,
                last_ts, msg_count, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 label=excluded.label, cwd=excluded.cwd,
                 project_dir=excluded.project_dir, jsonl_path=excluded.jsonl_path,
                 resume_command=excluded.resume_command, last_ts=excluded.last_ts,
                 msg_count=excluded.msg_count""",
            (sess["sessionId"], sess.get("label"), sess.get("cwd"),
             sess.get("projectDir"), sess.get("jsonlPath"),
             sess.get("resumeCommand"), sess.get("lastActivity") or now,
             sess.get("msgCount") or 0, now),
        )


def prune(days, keep_milestones=True):
    """구버전 호환용 평면 컷(현재는 쓰지 않음). 30일 넘은 것을 통째로 지운다."""
    cutoff = time.time() - days * 86400
    with _lock, _conn() as c:
        if keep_milestones:
            c.execute(
                "DELETE FROM snapshots WHERE last_ts < ? AND is_milestone=0", (cutoff,)
            )
        else:
            c.execute("DELETE FROM snapshots WHERE last_ts < ?", (cutoff,))


def prune_tiered(full_days, hourly_days, coarse_bucket_sec, dry_run=False):
    """계층적 보존 — 오래될수록 성기게 남긴다(옛 시점도 항상 되돌릴 수 있게).

    · 최근 full_days 일       : 전부 보존
    · full_days~hourly_days 일: 시간당 1개
    · 그 이전                 : coarse_bucket_sec 마다 1개
    · 마일스톤(하루 첫 스냅샷) : 항상 영구 보존

    이전 정책은 30일 평면 컷이라 그 이전이 마일스톤만 남아 '옛날 스냅샷 되돌리기'가 불가능했다.
    각 버킷에서 **가장 최신(MAX(id))** 하나를 남긴다.
    """
    now = time.time()
    cut_full = now - full_days * 86400
    cut_hourly = now - hourly_days * 86400
    stats = {}
    with _lock, _conn() as c:
        for name, lo, hi, bucket in (
            ("hourly", cut_hourly, cut_full, 3600),
            ("coarse", None, cut_hourly, coarse_bucket_sec),
        ):
            rng = "last_ts < ?" + (" AND last_ts >= ?" if lo is not None else "")
            params = [hi] + ([lo] if lo is not None else [])
            sql_where = f"is_milestone=0 AND {rng}"
            keep_sql = (f"SELECT MAX(id) FROM snapshots WHERE {sql_where} "
                        f"GROUP BY CAST(last_ts / {int(bucket)} AS INTEGER)")
            if dry_run:
                n = c.execute(
                    f"SELECT COUNT(*) n FROM snapshots WHERE {sql_where} "
                    f"AND id NOT IN ({keep_sql})", params * 2
                ).fetchone()["n"]
            else:
                cur = c.execute(
                    f"DELETE FROM snapshots WHERE {sql_where} AND id NOT IN ({keep_sql})",
                    params * 2)
                n = cur.rowcount
            stats[name] = n
    return stats


def get_meta(key, default=None):
    with _lock, _conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_meta(key, value):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

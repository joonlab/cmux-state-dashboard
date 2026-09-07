"""실행 중인 claude 세션의 건강도(유휴 정도) 조회.

판정 로직은 ~/.claude/scripts/claude-session-reaper.py 를 그대로 import 해서 쓴다.
대시보드에 규칙을 복제하면 임계값이 갈라지므로, 단일 구현을 유지한다
(복원 엔진을 restore.py 하나로 두는 것과 같은 원칙).

대시보드는 관측자다 — 여기서는 종료하지 않는다. 종료는 사람이 목록을 보고
터미널에서 `claude-session-reaper.py --hours 24 --reap --yes` 로 실행한다.
"""
import importlib.util
import os

REAPER_PATH = os.path.expanduser("~/.claude/scripts/claude-session-reaper.py")

_mod = None


def _reaper():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location("claude_session_reaper", REAPER_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"reaper 를 찾을 수 없음: {REAPER_PATH}")
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod


def available() -> bool:
    return os.path.isfile(REAPER_PATH)


def collect(hours: float | None = None) -> dict:
    """세션별 유휴 상태 + 요약. hours 는 '종료대상' 임계(기본 reaper 기본값)."""
    r = _reaper()
    h = float(hours) if hours else r.DEFAULT_HOURS
    rows = r.collect(h)

    def bucket(v):
        return [x for x in rows if x["verdict"] == v]

    return {
        "available": True,
        "thresholdHours": h,
        "candidateHours": r.CANDIDATE_HOURS,
        "sessions": [
            {
                "pid": x["pid"],
                "tty": x["tty"],
                "ttyIdleHours": round(x["tty_idle"], 1) if x["tty_idle"] is not None else None,
                "sessionIdleHours": round(x["session_idle"], 1) if x["session_idle"] is not None else None,
                "rssMb": round(x["rss_mb"]),
                "verdict": x["verdict"],
                "sessionId": x["session_id"],
                "resumeRegistered": x["resume_registered"],
                "preview": x["preview"],
                "etime": x["etime"],
            }
            for x in rows
        ],
        "summary": {
            "total": len(rows),
            "totalGb": round(sum(x["rss_mb"] for x in rows) / 1024, 1),
            "reap": len(bucket("reap")),
            "reapGb": round(sum(x["rss_mb"] for x in bucket("reap")) / 1024, 1),
            "candidate": len(bucket("candidate")),
            "candidateGb": round(sum(x["rss_mb"] for x in bucket("candidate")) / 1024, 1),
            "active": len(bucket("active")),
        },
        "reapCommand": f"python3 {REAPER_PATH} --hours {h:.0f} --reap",
    }

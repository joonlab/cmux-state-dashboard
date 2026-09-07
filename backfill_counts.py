"""스냅샷 목록/피커 카운트 백필 — '복원 가능 총량' 기준으로 재계산.

배경(2026-08-04): 2026-07-23 에 피커 카운트를 복원 원천인 네이티브 레이아웃 기준으로
통일했는데, cmux 가 세션 JSON 에 기록하지 않은 창은 거기서 통째로 빠진다. 그 결과
"창 3개를 쓰고 있었는데 대시보드는 창 2개만 보여준다"는 상태가 됐고, 유실이 조용히
진행됐다. 이제 그런 창도 tree 스냅샷에서 합성 복원하므로 카운트에 포함한다.

    .venv/bin/python backfill_counts.py            # 전체 (사전검사로 대부분 즉시 스킵)
    .venv/bin/python backfill_counts.py --limit 300 --dry-run

되돌리기: 이 스크립트는 n_windows/n_workspaces/n_sessions 세 컬럼만 바꾼다.
원래(네이티브 기준) 값은 normalized_json 의 layoutStats 에 그대로 남아 있어 언제든 재산출 가능.
"""
import argparse
import json
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

import claude_index  # noqa: E402
import db  # noqa: E402
import layout  # noqa: E402
import restore  # noqa: E402


def has_tree_only(norm, lw):
    """합성 없이 'tree 에만 있는 창'이 있는지 싸게 판정(문자열 집합 연산만).

    ⚠️ 판정 규칙은 layout.synthesize_tree_windows 와 **반드시 동일**해야 한다.
    (초판은 UUID 대조만 해서, workspaceId 가 없는 구 스냅샷을 전부 tree-only 로 오판했다.)
    """
    tree = norm.get("windows") or []
    if not tree:
        return False
    native_ids = layout.native_workspace_ids(lw)
    native_titles = layout.native_workspace_titles(lw)
    for w in tree:
        wss = w.get("workspaces") or []
        if not wss:
            continue
        if native_ids and any(str(ws.get("id") or "").lower() in native_ids for ws in wss):
            continue
        if not native_ids and native_titles:
            keys = {claude_index.normalize_title(ws.get("title")) for ws in wss}
            keys.discard("")
            if keys and len(keys & native_titles) * 2 >= len(keys):
                continue
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="최근 N개만 (0=전체)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with db._lock, db._conn() as c:
        q = "SELECT id FROM snapshots ORDER BY id DESC"
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        ids = [r["id"] for r in c.execute(q).fetchall()]

    print(f"대상 {len(ids)}건 {'(dry-run)' if args.dry_run else ''}", flush=True)
    t0 = time.time()
    scanned = affected = changed = 0
    for sid in ids:
        scanned += 1
        snap = db.get_snapshot(sid)
        if not snap:
            continue
        norm = snap["normalized"]
        stored = norm.get("layoutWindows")
        cur = (snap.get("n_windows"), snap.get("n_workspaces"), snap.get("n_sessions"))
        # 저장된 레이아웃이 있고 tree-only 도 없으면 값은 stored 기준으로 확정된다.
        # ⚠️ 그래도 **DB 값과 실제로 대조한 뒤** 스킵해야 한다 — 과거 백필이 틀린 값을 써넣었을
        #    수 있고(실제로 1차 백필이 그랬다), "바뀔 리 없다"는 가정만 믿으면 오염이 그대로 남는다.
        # 저장본이 없는 구 스냅샷은 restore 의 백업 폴백을 타야 하므로 계산 대상.
        if stored and not has_tree_only(norm, stored):
            st = layout.layout_stats(stored)
            if cur == (st["windows"], st["workspaces"], st["claude"]):
                continue
        affected += 1
        # ★ API(/api/recovery)와 **같은 경로**로 계산해야 피커 숫자와 본문이 일치한다.
        #   (초판은 저장된 layoutWindows 만 봐서 폴백을 타는 구 스냅샷에서 값이 어긋났다.)
        lw = restore.load_layout_windows({"normalized": norm, "id": sid})
        if not lw:
            continue
        st = layout.layout_stats(lw)
        new = (st["windows"], st["workspaces"], st["claude"])
        old = cur
        if tuple(old) == new:
            continue
        changed += 1
        print(f"  #{sid}  창{old[0]}·WS{old[1]}·C{old[2]}  →  창{new[0]}·WS{new[1]}·C{new[2]}",
              flush=True)
        if not args.dry_run:
            with db._lock, db._conn() as c:
                c.execute(
                    "UPDATE snapshots SET n_windows=?, n_workspaces=?, n_sessions=? WHERE id=?",
                    (*new, sid))
        if scanned % 2000 == 0:
            print(f"  … {scanned}/{len(ids)}  ({time.time()-t0:.0f}s)", flush=True)

    # ⚠️ affected = '사전검사를 통과 못해 실제 계산을 탄 건수'이지 '값이 틀린 건수'가 아니다.
    #    (틀린 건수는 changed. dry-run 에서 changed==0 이면 DB 가 계산값과 일치 = 수렴.)
    print(f"\n스캔 {scanned} · 계산대상 {affected} · 갱신 {changed}  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()

"""보존 정책 전환 마이그레이션 — 계층적 솎아내기 + normalized_json 압축 + VACUUM.

배경(2026-08-05): 기존 정책은 `RETENTION_DAYS=30` 평면 컷이라 30일이 지나면 마일스톤만
남았다 → "언제든 옛날 스냅샷으로 되돌린다"가 성립하지 않았다. 계층적 보존으로 바꾸면
오래된 구간도 성기게나마 영구히 남는다. 함께 본문을 압축해(실측 4.4배) 보존 확대의
용량 부담을 상쇄한다.

    .venv/bin/python migrate_retention.py --dry-run
    .venv/bin/python migrate_retention.py

⚠️ 삭제를 수반한다. 실행 전 data/snapshots.db 를 백업할 것.
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-prune", action="store_true", help="솎아내기 없이 압축만")
    args = ap.parse_args()

    before_bytes = os.path.getsize(config.DB_PATH)
    total = db.count_snapshots()
    print(f"시작: {total:,}건 · {before_bytes/1e9:.2f}GB")

    # 1) 계층적 솎아내기
    if not args.skip_prune:
        st = db.prune_tiered(config.RETAIN_FULL_DAYS, config.RETAIN_HOURLY_DAYS,
                             config.RETAIN_COARSE_BUCKET_SEC, dry_run=args.dry_run)
        print(f"  솎아내기: 시간당구간 {st['hourly']:,} + 성긴구간 {st['coarse']:,} "
              f"= {sum(st.values()):,}건 {'(dry-run)' if args.dry_run else '삭제'}")

    # 2) 본문 압축(미압축 행만) — 큰 DB 를 한 트랜잭션에 담지 않도록 배치 처리
    t0 = time.time()
    done = skipped = 0
    with db._lock:
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        ids = [r["id"] for r in c.execute("SELECT id FROM snapshots ORDER BY id").fetchall()]
        for i, sid in enumerate(ids):
            r = c.execute("SELECT normalized_json FROM snapshots WHERE id=?", (sid,)).fetchone()
            if r is None:
                continue
            v = r["normalized_json"]
            if isinstance(v, bytes):        # 이미 압축됨
                skipped += 1
                continue
            if args.dry_run:
                done += 1
                continue
            try:
                blob = db.encode_normalized(db.decode_normalized(v))
            except Exception as e:
                print(f"    #{sid} 스킵(디코드 실패): {e}")
                continue
            c.execute("UPDATE snapshots SET normalized_json=? WHERE id=?", (blob, sid))
            done += 1
            if done % 500 == 0:
                c.commit()
                print(f"    … 압축 {done:,}건 ({time.time()-t0:.0f}s)", flush=True)
        if not args.dry_run:
            c.commit()
        c.close()
    print(f"  압축: {done:,}건 {'(dry-run)' if args.dry_run else '완료'} · 이미압축 {skipped:,}건 "
          f"({time.time()-t0:.0f}s)")

    # 3) VACUUM — 삭제/압축으로 생긴 빈 페이지를 실제 파일 크기에 반영
    if not args.dry_run:
        t0 = time.time()
        c = sqlite3.connect(config.DB_PATH, timeout=600)
        c.execute("VACUUM")
        c.close()
        after = os.path.getsize(config.DB_PATH)
        print(f"  VACUUM: {before_bytes/1e9:.2f}GB → {after/1e9:.2f}GB "
              f"({before_bytes/max(after,1):.1f}배 축소, {time.time()-t0:.0f}s)")
        print(f"\n최종: {db.count_snapshots():,}건 "
              f"(마일스톤 {db.count_snapshots(milestones_only=True)})")


if __name__ == "__main__":
    main()

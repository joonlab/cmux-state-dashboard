#!/usr/bin/env python3
"""데모 모드로 관제실을 띄운다 — cmux 도, macOS 도 필요 없다.

    python run_demo.py                # → http://localhost:7788
    python run_demo.py --port 7801    # 진짜 대시보드가 이미 7788 을 쓰고 있을 때

합성 상태(`demo_fixture.py`)를 실제 API 와 **똑같은 모양**으로 내려 주므로 화면에 그려지는
건 진짜 `nav.js`/`board.css` 다. cmux 를 읽지도, 건드리지도, DB 를 만들지도 않는다.

끌어다 놓기·고정 토글·탭 이동은 데모에서도 **실제로 동작한다**(인메모리 상태를 바꾼다).
흐트러뜨렸으면 `curl -XPOST localhost:7788/api/demo/reset` 으로 되돌린다.
"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(description="cmux 관제실 — 데모 모드")
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--host", default="127.0.0.1",
                    help="같은 망의 다른 기기에서 보려면 0.0.0.0")
    args = ap.parse_args()

    os.environ["CMUX_DASH_DEMO"] = "1"
    os.environ["CMUX_DASH_PORT"] = str(args.port)

    try:
        import uvicorn
    except ImportError:
        sys.exit("의존성이 없습니다:  pip install -r requirements.txt")

    print(f"\n  cmux 관제실 · 데모 모드   http://{args.host}:{args.port}\n"
          f"  합성 데이터입니다 — 이 기계의 cmux 는 읽지 않습니다.\n"
          f"  내용을 바꾸려면 demo_fixture.py 하나만 고치면 됩니다.\n")
    uvicorn.run("app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

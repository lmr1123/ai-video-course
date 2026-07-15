#!/usr/bin/env python3
"""从项目根目录启动只供本机访问的静态服务器。"""

from __future__ import annotations

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def serve(port: int = 8737, video_id: str | None = None) -> None:
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    suffix = f"?id={video_id}" if video_id else ""
    print(f"本地播放器：http://localhost:{port}/prototype/local-player/{suffix}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8737)
    parser.add_argument("--video-id")
    args = parser.parse_args()
    serve(args.port, args.video_id)


if __name__ == "__main__":
    main()

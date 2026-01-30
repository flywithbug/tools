from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from _share.tool_spec import tool, opt, ex


BOX_TOOL = tool(
    id="box.ai_tm",
    name="box_ai_tm",
    category="ai",
    summary="本地 AI TM 服务：扫描本地文件并提供 API（无数据库）",
    usage=[
        "box_ai_tm --help",
        "box_ai_tm server",
        "box_ai_tm server --workspace .",
        "box_ai_tm server --port 37123 --open",
    ],
    options=[
        opt("server", "启动本地服务"),
        opt("--host", "监听地址（默认 127.0.0.1）"),
        opt("--port", "监听端口（默认 37123）"),
        opt("--workspace", "工作区根目录（默认当前目录）"),
        opt("--open", "启动后打开 WebUI（如存在）"),
        opt("--no-webui", "不托管静态 WebUI"),
    ],
    examples=[
        ex("box_ai_tm server", "启动服务（默认 workspace=当前目录）"),
        ex("box_ai_tm server --workspace ~/proj/app", "指定工作区启动"),
        ex("box_ai_tm server --port 40001 --open", "换端口并自动打开页面"),
    ],
    dependencies=[
        "fastapi",
        "uvicorn",
    ],
    docs="README.md",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box_ai_tm",
        description="box_ai_tm: 本地 AI TM 服务（轻量）",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("server", help="启动本地服务")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=37123)
    sp.add_argument("--workspace", default=".", help="工作区根目录（默认当前目录）")
    sp.add_argument("--open", action="store_true", help="启动后打开 WebUI")
    sp.add_argument("--no-webui", action="store_true", help="禁用静态 WebUI 托管")
    sp.set_defaults(handler=cmd_server)

    return p


def cmd_server(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from ai_tm.server.app import create_app

    host: str = args.host
    port: int = args.port
    workspace = str(Path(args.workspace).expanduser().resolve())
    enable_webui = not bool(args.no_webui)

    app = create_app(workspace=workspace, enable_webui=enable_webui)

    if args.open:
        import threading
        import webbrowser

        def _open():
            webbrowser.open(f"http://{host}:{port}/")

        threading.Timer(0.8, _open).start()

    try:
        import uvicorn
    except Exception as e:
        print("缺少依赖：uvicorn。请在项目依赖中加入 uvicorn 或 uvicorn[standard]。", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 2

    uvicorn.run(app, host=host, port=port, log_level=os.getenv("BOX_AI_TM_LOG", "info"))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()

    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return int(handler(parser, args))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import data
from . import translate  # 如果你暂时没实现 translate.py，可先注释掉相关分支

from _share.tool_spec import tool, opt, ex

BOX_TOOL = tool(
    id="flutter.box_slang_i18n",
    name="box_slang_i18n",
    category="flutter",
    summary=(
        "Flutter slang i18n 资源管理 CLI：基于默认模板生成/校验配置（保留注释），"
        "支持 sort/doctor，以及 AI 增量翻译（translate）"
    ),
    usage=[
        "box_slang_i18n",
        "box_slang_i18n init",
        "box_slang_i18n sort",
        "box_slang_i18n doctor",
        "box_slang_i18n translate",
        "box_slang_i18n translate --no-incremental",
        "box_slang_i18n --config slang_i18n.yaml",
        "box_slang_i18n --project-root path/to/project",
    ],
    options=[
        opt("command", "子命令：menu/init/sort/translate/doctor（默认 menu）"),
        opt("--config", "配置文件路径（默认 slang_i18n.yaml，基于 project-root）"),
        opt("--project-root", "项目根目录（默认当前目录）"),
        opt("--i18n-dir", "覆盖配置中的 i18nDir（相对 project-root 或绝对路径）"),
        opt("--no-incremental", "translate：关闭增量翻译，改为全量翻译"),
    ],
    examples=[
        ex(
            "box_slang_i18n init",
            "生成/校验配置文件（保留模板注释），并确保 languages.json 存在，同时创建 i18nDir",
        ),
        ex("box_slang_i18n", "进入交互菜单（启动会优先校验配置 + 检查 i18nDir 目录）"),
        ex("box_slang_i18n sort", "对 i18n JSON 执行排序（按工具规则）"),
        ex("box_slang_i18n doctor", "环境/结构诊断：配置合法、目录结构、文件命名、@@locale/flat 等"),
        ex("box_slang_i18n translate", "AI 增量翻译：只翻译缺失 key（排除 @@locale）"),
        ex("box_slang_i18n translate --no-incremental", "AI 全量翻译：按 source 覆盖生成 target 的翻译内容"),
        ex("box_slang_i18n --project-root ./app --config slang_i18n.yaml init", "在指定项目根目录下初始化"),
    ],
    dependencies=[
        "PyYAML>=6.0",
        "openai>=1.0.0",
    ],
    docs="README.md",  # 可省略：默认就是 README.md
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="box_slang_i18n")
    p.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=["menu", "init", "sort", "translate", "doctor"],
        help="子命令",
    )
    p.add_argument(
        "--config",
        default=data.DEFAULT_TEMPLATE_NAME,
        help=f"配置文件路径（默认 {data.DEFAULT_TEMPLATE_NAME}，基于 project-root）",
    )
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--i18n-dir", default=None, help="覆盖配置中的 i18nDir（相对 project-root 或绝对路径）")
    p.add_argument("--no-incremental", action="store_true", help="translate：关闭增量翻译（全量翻译）")
    return p


def run_menu(cfg_path: Path, project_root: Path) -> int:
    menu = [
        ("sort",      "排序"),
        ("translate", "翻译（默认增量）"),
        ("doctor",    "环境诊断"),
        ("init",      "生成/校验配置"),
    ]

    while True:
        print("\n=== box_slang_i18n ===")
        for idx, (cmd, label) in enumerate(menu, start=1):
            print(f"{idx}. {cmd:<10} {label}")
        print("0. exit       退出")

        choice = input("> ").strip()
        if choice == "0":
            return 0

        if not choice.isdigit():
            print("无效选择")
            continue

        idx = int(choice)
        if not (1 <= idx <= len(menu)):
            print("无效选择")
            continue

        cmd = menu[idx - 1][0]
        argv = ["box_slang_i18n", cmd, "--config", str(cfg_path), "--project-root", str(project_root)]
        return main(argv)


def _resolve_i18n_dir_override(project_root: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (project_root / p).resolve()


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = build_parser().parse_args(argv[1:])

    project_root = Path(args.project_root).resolve()
    cfg_path = (project_root / args.config).resolve()

    # 1) init：允许无配置，负责生成/校验，并确保 languages.json + i18nDir 存在
    if args.command == "init":
        try:
            data.init_config(project_root=project_root, cfg_path=cfg_path)
            print(f"✅ init 完成：{cfg_path}")
            return 0
        except Exception as e:
            print(f"❌ init 失败：{e}")
            return 1

    # 2) 其他命令：启动后优先校验配置（包括 menu），并检查 i18nDir 是否存在
    try:
        data.assert_config_ok(cfg_path, project_root=project_root, check_i18n_dir_exists=True)
    except data.ConfigError as e:
        print(str(e))
        return 1

    # 3) 校验通过才加载配置对象（此时 cfg.i18n_dir 为绝对路径）
    try:
        cfg = data.load_config(cfg_path, project_root=project_root)
    except Exception as e:
        print(f"❌ 配置加载失败：{e}")
        return 1

    if args.i18n_dir:
        cfg = data.override_i18n_dir(cfg, _resolve_i18n_dir_override(project_root, args.i18n_dir))

    if args.command == "menu":
        return run_menu(cfg_path=cfg_path, project_root=project_root)

    if args.command == "sort":
        data.run_sort(cfg)
        return 0

    if args.command == "doctor":
        return data.run_doctor(cfg)

    if args.command == "translate":
        incremental = not args.no_incremental
        translate.run_translate(cfg, incremental=incremental)
        return 0

    print("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

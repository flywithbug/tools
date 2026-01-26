#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import data
from . import translate
from . import swift_codegen


BOX_TOOL = {
    "id": "ios.strings_i18n",
    "name": "box_strings_i18n",
    "category": "iOS",
    "summary": "iOS .strings 多语言治理/翻译/Swift 代码生成 CLI",
    "usage": [
        "box_strings_i18n",
        "box_strings_i18n init",
        "box_strings_i18n doctor",
        "box_strings_i18n sort",
        "box_strings_i18n translate",
        "box_strings_i18n gen-swift",
        "box_strings_i18n --config strings_i18n.yaml",
        "box_strings_i18n --project-root path/to/project",
    ],
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="box_strings_i18n")
    p.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=["menu", "init", "doctor", "sort", "translate", "gen-swift"],
        help="子命令",
    )
    p.add_argument(
        "--config",
        default=data.DEFAULT_TEMPLATE_NAME,
        help=f"配置文件路径（默认 {data.DEFAULT_TEMPLATE_NAME}，基于 project-root）",
    )
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--no-incremental", action="store_true", help="translate：关闭增量翻译（全量翻译）")
    return p

def run_menu(cfg_path: Path, project_root: Path) -> int:
    # menu 进入先 doctor gate（启动即检查）
    try:
        data.assert_config_ok(cfg_path, project_root=project_root)
        cfg = data.load_config(cfg_path, project_root=project_root)
    except data.ConfigError as e:
        print(str(e))
        return 1
    except Exception as e:
        print(f"❌ 配置加载失败：{e}")
        return 1

    rc = data.run_doctor(cfg)
    if rc != 0:
        print("❌ doctor 未通过，menu 中止。")
        return rc

    menu = [
        ("doctor",    "环境诊断"),
        ("sort",      "排序写回（Base 保注释，其它无注释）"),
        ("translate", "翻译（默认增量）"),
        ("gen-swift", "生成 L10n.swift"),
        ("init",      "生成/校验配置"),
    ]

    while True:
        print("\n=== box_strings_i18n ===")
        for idx, (cmd, label) in enumerate(menu, start=1):
            print(f"{idx}. {cmd:<10} {label}")
        print("0. exit       退出")

        choice = input("> ").strip()
        if choice == "0":
            return 0

        if not choice.isdigit():
            print("无效选择")
            continue

        i = int(choice)
        if not (1 <= i <= len(menu)):
            print("无效选择")
            continue

        cmd = menu[i - 1][0]
        argv = ["box_strings_i18n", cmd, "--config", str(cfg_path), "--project-root", str(project_root)]
        return main(argv)


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = build_parser().parse_args(argv[1:])

    project_root = Path(args.project_root).resolve()
    cfg_path = (project_root / args.config).resolve()

    # 1) init：允许无配置（负责生成/校验）
    if args.command == "init":
        try:
            data.init_config(project_root=project_root, cfg_path=cfg_path)
            print(f"✅ init 完成：{cfg_path}")
            return 0
        except Exception as e:
            print(f"❌ init 失败：{e}")
            return 1

    # 2) 其他命令：启动优先校验配置（menu 也要校验）
    try:
        data.assert_config_ok(cfg_path, project_root=project_root)
    except data.ConfigError as e:
        print(str(e))
        return 1

    # 3) 加载配置对象
    try:
        cfg = data.load_config(cfg_path, project_root=project_root)
    except Exception as e:
        print(f"❌ 配置加载失败：{e}")
        return 1

    if args.command == "menu":
        return run_menu(cfg_path=cfg_path, project_root=project_root)

    if args.command == "doctor":
        return data.run_doctor(cfg)

    if args.command == "sort":
        return data.run_sort(cfg)

    if args.command == "translate":
        incremental = not args.no_incremental
        translate.run_translate(cfg, incremental=incremental)
        return 0

    if args.command == "gen-swift":
        swift_codegen.run_gen_swift(cfg)
        return 0

    print("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

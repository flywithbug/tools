#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import data
from . import translate

from _share.tool_spec import tool, opt, ex 

BOX_TOOL = tool(
    id="ios.box_strings_i18n",
    name="box_strings_i18n",
    category="ios",
    summary=(
        "iOS .strings i18n 资源管理 CLI（骨架）：生成/校验配置（保留注释），"
        "支持 doctor/sort，以及 AI 翻译入口（translate，待实现）"
    ),
    usage=[
        "box_strings_i18n",
        "box_strings_i18n init",
        "box_strings_i18n sort",
        "box_strings_i18n doctor",
        "box_strings_i18n gen",
        "box_strings_i18n translate",
        "box_strings_i18n translate --no-incremental",
        "box_strings_i18n --config strings_i18n.yaml",
        "box_strings_i18n --project-root path/to/project",
    ],
    options=[
        opt("command", "子命令：menu/init/sort/translate/doctor（默认 menu）"),
        opt("--config", "配置文件路径（默认 strings_i18n.yaml，基于 project-root）"),
        opt("--project-root", "项目根目录（默认当前目录）"),
        opt("--no-incremental", "translate：关闭增量翻译，改为全量翻译"),
        opt("--strings-file", "gen：从 Base.lproj 下的哪个 .strings 文件生成（默认 Localizable.strings）"),
        opt("--swift-out", "gen：L10n.swift 输出路径（默认 <lang_root>/L10n.swift；相对路径按 lang_root 解析）"),
    ],
    examples=[
        ex(
            "box_strings_i18n init",
            "生成/校验配置文件（保留模板注释），并从本地 languages.json 读取 target_locales，同时确保 lang_root 目录存在",
        ),
        ex("box_strings_i18n", "进入交互菜单（启动会优先校验配置 + 基础目录结构）"),
        ex("box_strings_i18n doctor", "环境/结构诊断（骨架：路径与 Base.lproj 检查）"),
        ex("box_strings_i18n sort", "排序（骨架：待实现 .strings key 排序与写回）"),
        ex("box_strings_i18n gen", "从 Base.lproj/Localizable.strings 生成 L10n.swift"),
        ex("box_strings_i18n translate", "翻译入口（骨架：待实现）"),
    ],
    dependencies=[
        "PyYAML>=6.0",
    ],
    docs="README.md",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="box_strings_i18n")
    p.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=["menu", "init", "sort", "translate", "doctor", "gen"],
        help="子命令",
    )
    p.add_argument(
        "--config",
        default=data.DEFAULT_TEMPLATE_NAME,
        help=f"配置文件路径（默认 {data.DEFAULT_TEMPLATE_NAME}，基于 project-root）",
    )
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--no-incremental", action="store_true", help="translate：关闭增量翻译（全量翻译）")
    p.add_argument("--strings-file", default="Localizable.strings", help="gen：Base.lproj 下输入 .strings 文件名")
    p.add_argument("--swift-out", default="L10n.swift", help="gen：输出 Swift 文件路径（默认写到 lang_root 下；相对路径按 lang_root）")
    return p


def run_menu(cfg_path: Path, project_root: Path) -> int:
    menu = [
        ("doctor",    "环境诊断"),
        ("sort",      "排序（TODO）"),
        ("translate", "翻译（TODO）"),
        ("gen",       "生成 L10n.swift"),
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

        idx = int(choice)
        if not (1 <= idx <= len(menu)):
            print("无效选择")
            continue

        cmd = menu[idx - 1][0]
        argv = ["box_strings_i18n", cmd, "--config", str(cfg_path), "--project-root", str(project_root)]
        return main(argv)


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = build_parser().parse_args(argv[1:])

    project_root = Path(args.project_root).resolve()
    cfg_path = (project_root / args.config).resolve()

    # 1) init：允许无配置，负责生成/校验，并确保 languages.json + lang_root 存在
    if args.command == "init":
        try:
            data.init_config(project_root=project_root, cfg_path=cfg_path)
            print(f"✅ init 完成：{cfg_path}")
            return 0
        except Exception as e:
            print(f"❌ init 失败：{e}")
            return 1

    # 2) 其他命令：启动后优先校验配置（包括 menu）
    try:
        data.assert_config_ok(cfg_path, project_root=project_root, check_paths_exist=True)
    except data.ConfigError as e:
        print(str(e))
        return 1

    # 3) 校验通过才加载配置对象
    try:
        cfg = data.load_config(cfg_path, project_root=project_root)
    except Exception as e:
        print(f"❌ 配置加载失败：{e}")
        return 1

    if args.command == "menu":
        return run_menu(cfg_path=cfg_path, project_root=project_root)

    if args.command == "doctor":
        return data.run_doctor(cfg)

    if args.command == "gen":
        try:
            # ✅ 产物约定：默认写到 lang_root 下面；相对路径也按 lang_root
            out_arg = Path(args.swift_out)
            out_path = out_arg if out_arg.is_absolute() else (cfg.lang_root / out_arg)
            out_path = out_path.resolve()
            fp = data.generate_l10n_swift(
                cfg,
                strings_filename=args.strings_file,
                out_path=out_path,
            )
            print(f"✅ 已生成：{fp}")
            return 0
        except Exception as e:
            print(f"❌ 生成失败：{e}")
            return 1

    if args.command == "sort":
        data.run_sort(cfg)
        return 0

    if args.command == "translate":
        incremental = not args.no_incremental
        translate.run_translate(cfg, incremental=incremental)
        return 0

    print("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

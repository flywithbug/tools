#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strings_i18n tool.py
CLI 入口：参数解析 + action 路由 + exit code
只保留 6 个 commands：
- init / doctor / sort / gen-l10n / translate-core / translate-target
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, List

from . import data
from . import translate as tr
from _share.tool_spec import tool, opt, ex

BOX_TOOL = tool(
    id="flutter.box_strings_i18n",
    name="box_strings_i18n",
    category="flutter",
    summary=(
        "iOS/Xcode .strings 多语言：排序（幂等）/生成 L10n.swift/翻译（core & target）/环境诊断/配置初始化（支持交互菜单）"
    ),
    usage=[
        "box_strings_i18n",
        "box_strings_i18n init",
        "box_strings_i18n sort",
        "box_strings_i18n gen-l10n",
        "box_strings_i18n doctor",
        "box_strings_i18n translate-core",
        "box_strings_i18n translate-target",
        "box_strings_i18n translate-core --no-incremental",
        "box_strings_i18n translate-target --no-incremental",
        "box_strings_i18n --config strings_i18n.yaml",
        "box_strings_i18n --project-root path/to/project",
    ],
    options=[
        opt("command", "子命令：menu/init/sort/gen-l10n/translate-core/translate-target/doctor（默认 menu）"),
        opt("--config", "配置文件路径（默认 strings_i18n.yaml，基于 project-root）"),
        opt("--project-root", "项目根目录（默认当前目录）"),
        opt("--i18n-dir", "覆盖配置中的语言目录根路径（相对 project-root 或绝对路径）"),
        opt("--no-incremental", "翻译：关闭增量翻译，改为全量覆盖（对 translate-core/translate-target 生效）"),
    ],
    examples=[
        ex(
            "box_strings_i18n init",
            "生成/校验配置文件（保留模板注释）；若不存在则创建，若存在则只校验；并确保语言目录存在",
        ),
        ex("box_strings_i18n", "进入交互菜单（启动会优先校验配置 + 检查目录结构）"),
        ex("box_strings_i18n sort", "对 Base 进行分组排序并对齐其它语言顺序（幂等、保留注释）"),
        ex("box_strings_i18n gen-l10n", "从 Base.lproj/Localizable.strings 生成 L10n.swift（按点号前缀分组）"),
        ex("box_strings_i18n doctor", "环境/结构诊断：依赖、配置合法、目录结构、Base 文件存在等"),
        ex("box_strings_i18n translate-core", "翻译（core）：Base.lproj → core_locales（默认增量）"),
        ex("box_strings_i18n translate-target", "翻译（target）：source_locale.lproj → target_locales（默认增量）"),
        ex("box_strings_i18n translate-core --no-incremental", "翻译（core）：全量覆盖生成"),
        ex("box_strings_i18n --project-root ./app --config strings_i18n.yaml init", "在指定项目根目录下初始化"),
    ],
    dependencies=[
        "PyYAML>=6.0",
        "openai>=1.0.0",
    ],
    docs="README.md",
)


EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD = 2


MENU = [
    ("gen-l10n",         "生成 L10n.swift（按点号前缀分组）"),
    ("sort",             "排序 Localizable.strings（Base 分组/2空行/注释跟随；其他语言只排序）"),
    ("translate-core",   "翻译（core）：Base.lproj → core_locales"),
    ("translate-target", "翻译（target）：source_locale.lproj → target_locales"),
    ("doctor",           "环境诊断"),
    ("init",             "生成/校验配置"),
]



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="strings_i18n",
        description="iOS/Xcode .strings：排序/翻译/L10n.swift 生成（结构对齐 strings_i18n）",
    )
    p.add_argument(
        "action",
        nargs="?",
        choices=[
            "init",
            "doctor",
            "sort",
            "gen-l10n",
            "translate-core",
            "translate-target",
        ],
        help="命令（不填可自行决定是否做交互菜单，这里骨架默认要求填写）",
    )
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--config", default=data.CONFIG_FILE, help="配置文件路径（默认 strings_i18n.yaml）")
    p.add_argument("--languages", default=data.LANG_FILE, help="languages.json 路径（默认 languages.json）")

    # 通用写入控制
    p.add_argument("--dry-run", action="store_true", help="预览模式（不写入任何文件）")

    # 翻译参数
    p.add_argument("--api-key", default=None, help="OpenAI API key（或环境变量 OPENAI_API_KEY）")
    p.add_argument("--model", default=None, help="模型（CLI 优先；不传则用配置/默认）")
    p.add_argument("--full", action="store_true", help="全量翻译（默认按配置增量）")

    # gen-l10n 输出路径
    p.add_argument("--l10n-out", default=None, help="L10n.swift 输出路径（默认写入 {lang_root}/L10n.swift）")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()

    cfg_path = Path(args.config).expanduser()
    if not cfg_path.is_absolute():
        cfg_path = (project_root / cfg_path).resolve()

    languages_path = Path(args.languages).expanduser()
    if not languages_path.is_absolute():
        languages_path = (project_root / languages_path).resolve()

    action = args.action
    if not action:
        print("❌ 该骨架默认要求指定 action（后续可加交互菜单）。可选：")
        for k, desc in MENU:
            print(f"  - {k:<15} {desc}")
        return EXIT_BAD

    dry = bool(args.dry_run)

    # init / doctor 可以在无 cfg 的情况下运行
    if action == "init":
        try:
            data.init_config(cfg_path=cfg_path, project_root=project_root, languages_path=languages_path)
            return EXIT_OK
        except Exception as e:
            print(str(e))
            return EXIT_BAD

    if action == "doctor":
        try:
            data.doctor(cfg_path=cfg_path, languages_path=languages_path, project_root=project_root, api_key=args.api_key)
            return EXIT_OK
        except SystemExit as e:
            return int(getattr(e, "code", EXIT_BAD))
        except Exception as e:
            print(str(e))
            return EXIT_BAD

    # 其余 action 必须有合法 cfg
    try:
        cfg = data.read_config_or_throw(cfg_path)
    except Exception as e:
        print(str(e))
        return EXIT_BAD

    if action == "sort":
        try:
            stats = data.sort_command(project_root=project_root, cfg=cfg, dry_run=dry)
            data.print_sort_summary(stats, dry_run=dry)
            return EXIT_OK
        except Exception as e:
            print(f"❌ sort 失败：{e}")
            return EXIT_FAIL

    if action == "gen-l10n":
        try:
            out = data.generate_l10n_swift(
                project_root=project_root,
                cfg=cfg,
                out_path_arg=args.l10n_out,
                dry_run=dry,
            )
            if dry:
                print("（dry-run：未写入）")
            else:
                print(f"✅ 已生成：{out}")
            return EXIT_OK
        except Exception as e:
            print(f"❌ gen-l10n 失败：{e}")
            return EXIT_FAIL

    # 翻译 action
    if action in ("translate-core", "translate-target"):
        try:
            model = data.pick_model(cli_model=args.model, cfg=cfg)
            full = bool(args.full) or (not bool(cfg.options.incremental_translate))
            api_key = tr.get_api_key(args.api_key)
            if not api_key:
                print("❌ 未提供 API Key（翻译需要）：--api-key 或环境变量 OPENAI_API_KEY")
                return EXIT_BAD

            if action == "translate-core":
                tr.translate_core(project_root=project_root, cfg=cfg, api_key=api_key, model=model, full=full, dry_run=dry)
            else:
                tr.translate_target(project_root=project_root, cfg=cfg, api_key=api_key, model=model, full=full, dry_run=dry)

            return EXIT_OK
        except Exception as e:
            print(f"❌ {action} 失败：{e}")
            return EXIT_FAIL

    print(f"❌ 未实现的 action：{action}")
    return EXIT_BAD


if __name__ == "__main__":
    raise SystemExit(main())

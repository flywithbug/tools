#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import data
from . import translate

from _share.tool_spec import tool, opt, ex


BOX_TOOL = tool(
    id="core.box_json_i18n",
    name="box_json_i18n",
    category="core",
    summary="JSON i18n 资源管理 CLI：init/sync/sort/doctor/translate（含默认启动 doctor）",
    usage=[
        "box_json_i18n",
        "box_json_i18n init",
        "box_json_i18n sync",
        "box_json_i18n sort",
        "box_json_i18n doctor",
        "box_json_i18n translate",
        "box_json_i18n translate --no-incremental",
        "box_json_i18n --config gpt_json.yaml",
        "box_json_i18n --project-root path/to/project",
    ],
    options=[
        opt("command", "子命令：menu/init/sync/sort/translate/doctor（默认 menu）"),
        opt("--config", f"配置文件路径（默认 {data.DEFAULT_TEMPLATE_NAME}）"),
        opt("--project-root", "项目根目录（默认当前目录）"),
        opt("--i18n-dir", "覆盖配置中的 i18nDir（相对 project-root 或绝对路径）"),
        opt("--yes", "sync/sort：自动执行创建/删除等操作（跳过交互确认）"),
        opt("--no-incremental", "translate：关闭增量翻译，改为全量翻译"),
        opt("--skip-doctor", "跳过启动时默认 doctor（不建议日常使用）"),
    ],
    examples=[
        ex("box_json_i18n", "进入 menu；启动前会自动 doctor，有问题就提示"),
        ex("box_json_i18n sort", "自动先 sync，再执行 sort"),
        ex("box_json_i18n translate", "目标文件缺失会自动创建后再翻译"),
    ],
    docs="README.md",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="box_json_i18n")
    p.add_argument(
        "command",
        nargs="?",
        default="menu",
        choices=["menu", "init", "sync", "sort", "translate", "doctor"],
        help="子命令",
    )
    p.add_argument("--config", default=data.DEFAULT_TEMPLATE_NAME, help="配置文件路径（基于 project-root）")
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--i18n-dir", default=None, help="覆盖配置中的 i18nDir（相对 project-root 或绝对路径）")
    p.add_argument("--yes", action="store_true", help="自动执行创建/删除等操作（跳过交互确认）")
    p.add_argument("--no-incremental", action="store_true", help="translate：关闭增量翻译（全量翻译）")
    p.add_argument("--skip-doctor", action="store_true", help="跳过启动时默认 doctor")
    return p


def _resolve_i18n_dir_override(project_root: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (project_root / p).resolve()


def _startup_doctor(cfg: data.Config) -> int:
    """
    启动时默认执行 doctor：
    - 有问题：提示并阻止继续（除非你未来加 --force）
    - 无问题：放行
    """
    result = data.run_doctor(cfg)
    if result != 0:
        print("❌ doctor 检查未通过：请先修复上述问题。")
        return result
    return 0


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = build_parser().parse_args(argv[1:])

    project_root = Path(args.project_root).resolve()
    cfg_path = (project_root / args.config).resolve()

    # init：允许无配置
    if args.command == "init":
        try:
            data.init_config(project_root=project_root, cfg_path=cfg_path, yes=args.yes)
            print(f"✅ init 完成：{cfg_path}")
            return 0
        except Exception as e:
            print(f"❌ init 失败：{e}")
            return 1

    # 其余命令：必须有配置
    try:
        data.assert_config_ok(cfg_path, project_root=project_root, check_i18n_dir_exists=False)
    except data.ConfigError as e:
        print(str(e))
        return 1

    cfg = data.load_config(cfg_path, project_root=project_root)
    if args.i18n_dir:
        cfg = data.override_i18n_dir(cfg, _resolve_i18n_dir_override(project_root, args.i18n_dir))

    # 启动时默认 doctor（你要求：有问题提示，无问题放行）
    if not args.skip_doctor and args.command != "doctor":
        rc = _startup_doctor(cfg)
        if rc != 0:
            return rc

    # menu
    if args.command == "menu":
        return data.run_menu(cfg_path=cfg_path, project_root=project_root)

    # 显式 doctor
    if args.command == "doctor":
        return data.run_doctor(cfg)

    # sync
    if args.command == "sync":
        return data.run_sync(cfg, yes=args.yes)

    # sort：自动执行 sync（你要求）
    if args.command == "sort":
        sync_rc = data.run_sync(cfg, yes=args.yes)
        if sync_rc != 0 and not args.yes:
            # 同步阶段发现缺失但没 --yes，通常意味着用户还没创建；阻止继续 sort 更安全
            print("❌ sort 前 sync 检测到缺失且未创建（未使用 --yes），已停止。")
            return sync_rc
        return data.run_sort(cfg, yes=args.yes)

    # translate：若缺失目标文件夹/文件，自动创建（你要求）
    if args.command == "translate":
        incremental = not args.no_incremental
        return translate.run_translate(cfg, incremental=incremental, auto_create_targets=True, yes=args.yes)

    print("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

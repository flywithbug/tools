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
    summary="JSON i18n 资源管理 CLI：init/sync/sort/doctor/translate（启动默认 doctor）",
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
        opt("--skip-doctor", "跳过启动时默认 doctor（不建议）"),
    ],
    examples=[
        ex("box_json_i18n init", "使用同目录模板 gpt_json.yaml 初始化/校验配置"),
        ex("box_json_i18n sort", "自动先 sync，再执行 sort"),
        ex("box_json_i18n translate", "目标目录/文件缺失会自动创建后再翻译"),
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


def main(argv=None) -> int:
    argv = argv or sys.argv
    args = build_parser().parse_args(argv[1:])

    project_root = Path(args.project_root).resolve()
    cfg_path = (project_root / args.config).resolve()

    # init：允许无配置；若不存在则用包内模板创建
    if args.command == "init":
        try:
            data.init_config(project_root=project_root, cfg_path=cfg_path, yes=args.yes)
            print(f"✅ init 完成：{cfg_path}")
            return 0
        except Exception as e:
            print(f"❌ init 失败：{e}")
            return 1

    # 其它命令：必须有配置
    try:
        data.assert_config_ok(cfg_path, project_root=project_root)
    except data.ConfigError as e:
        print(str(e))
        return 1

    cfg = data.load_config(cfg_path, project_root=project_root)
    if args.i18n_dir:
        cfg = data.override_i18n_dir(cfg, _resolve_i18n_dir_override(project_root, args.i18n_dir))

    # 启动默认 doctor：发现问题不退出（仅提示建议），继续执行用户命令
    if not args.skip_doctor and args.command not in ("doctor",):
        rc = data.run_doctor(cfg)
        if rc != 0:
            print("⚠️ doctor 发现问题：建议按需执行以下操作后再重试：")
            print(f"  - 查看详情：box_json_i18n doctor --config {cfg_path} --project-root {project_root}")
            print(f"  - 自动补齐目录/文件：box_json_i18n sync --yes --config {cfg_path} --project-root {project_root}")
            print(f"  - 重新生成/校验配置：box_json_i18n init --config {cfg_path} --project-root {project_root}")
            print("⚠️ 将继续执行当前命令（可能会因为上述问题导致后续失败）。")

    if args.command == "menu":
        return data.run_menu(cfg_path=cfg_path, project_root=project_root)

    if args.command == "doctor":
        return data.run_doctor(cfg)

    if args.command == "sync":
        return data.run_sync(cfg, yes=args.yes)

    if args.command == "sort":
        # sort 自动先 sync
        sync_rc = data.run_sync(cfg, yes=args.yes)
        if sync_rc != 0 and not args.yes:
            print("❌ sort 前 sync 检测到缺失且未创建（未使用 --yes），已停止。")
            return sync_rc
        return data.run_sort(cfg, yes=args.yes)

    if args.command == "translate":
        incremental = not args.no_incremental
        return translate.run_translate(cfg, incremental=incremental, auto_create_targets=True)

    print("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

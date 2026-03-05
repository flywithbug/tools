from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

from . import data

_VALID_ASSET_SUFFIXES = (".imageset", ".symbolset")
_IGNORE_ASSET_NAMES = {"AppIcon", "LaunchImage"}
_SWIFT_KEYWORDS = {
    "associatedtype",
    "class",
    "deinit",
    "enum",
    "extension",
    "fileprivate",
    "func",
    "import",
    "init",
    "inout",
    "internal",
    "let",
    "open",
    "operator",
    "private",
    "protocol",
    "public",
    "rethrows",
    "static",
    "struct",
    "subscript",
    "typealias",
    "var",
    "break",
    "case",
    "continue",
    "default",
    "defer",
    "do",
    "else",
    "fallthrough",
    "for",
    "guard",
    "if",
    "in",
    "repeat",
    "return",
    "switch",
    "where",
    "while",
    "as",
    "Any",
    "catch",
    "false",
    "is",
    "nil",
    "super",
    "self",
    "Self",
    "throw",
    "throws",
    "true",
    "try",
    "_",
    "associativity",
    "convenience",
    "dynamic",
    "didSet",
    "final",
    "get",
    "infix",
    "indirect",
    "lazy",
    "left",
    "mutating",
    "none",
    "nonmutating",
    "optional",
    "override",
    "postfix",
    "precedence",
    "prefix",
    "Protocol",
    "required",
    "right",
    "set",
    "Type",
    "unowned",
    "weak",
    "willSet",
    "some",
    "actor",
    "async",
    "await",
}
_IDENT_RE = re.compile(r"[^A-Za-z0-9_]+")


def _scan_xcassets(assets_root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for root, dirs, _files in os.walk(assets_root):
        for d in dirs:
            if not d.endswith(_VALID_ASSET_SUFFIXES):
                continue
            name = d
            for suf in _VALID_ASSET_SUFFIXES:
                if name.endswith(suf):
                    name = name[: -len(suf)]
                    break
            if not name or name in _IGNORE_ASSET_NAMES:
                continue
            rel = Path(root, d).relative_to(assets_root).as_posix()
            out.setdefault(name, []).append(rel)
    return out


def _sanitize_case_name(name: str, prefix: str) -> str:
    case = _IDENT_RE.sub("_", name.strip())
    case = re.sub(r"__+", "_", case).strip("_")
    if not case:
        case = "_"
    if case[0].isdigit():
        case = f"_{case}"
    if case in _SWIFT_KEYWORDS:
        case = f"_{case}"
    case = f"{prefix}{case}"
    if case[0].isdigit():
        case = f"_{case}"
    return case


def _ensure_unique(names_map: dict[str, str]) -> dict[str, str]:
    used = set()
    result: dict[str, str] = {}
    for asset_name in sorted(names_map.keys(), key=lambda s: (s.lower(), s)):
        base = names_map[asset_name]
        cand = base
        i = 2
        while cand in used:
            cand = f"{base}_{i}"
            i += 1
        used.add(cand)
        result[asset_name] = cand
    return result


def _print_duplicate_assets(dups: dict[str, list[str]]) -> None:
    print("⚠️ 发现同名资源：")
    for name, paths in sorted(dups.items(), key=lambda kv: kv[0]):
        print(f"- {name}")
        for rp in sorted(paths):
            print(f"  - {rp}")


def _build_all_assets(cfg: data.StringsI18nConfig) -> dict[str, list[str]]:
    assets_paths = list(cfg.assets_paths or [])
    if not assets_paths:
        raise data.ConfigError("assets_paths 为空，无法扫描资源")

    all_assets: dict[str, list[str]] = {}
    for ap in assets_paths:
        scanned = _scan_xcassets(ap)
        for name, rels in scanned.items():
            all_assets.setdefault(name, []).extend([f"{ap.name}/{r}" for r in rels])
    return all_assets


def find_duplicate_assets(cfg: data.StringsI18nConfig) -> dict[str, list[str]]:
    all_assets = _build_all_assets(cfg)
    return {k: v for k, v in all_assets.items() if len(v) > 1}


def check_duplicates(cfg: data.StringsI18nConfig) -> int:
    all_assets = _build_all_assets(cfg)
    if not all_assets:
        print("⚠️ 未发现任何 *.imageset / *.symbolset")
        return 0
    dups = {k: v for k, v in all_assets.items() if len(v) > 1}
    if not dups:
        print("✅ 未发现同名资源")
        return 0
    _print_duplicate_assets(dups)
    return 1


def generate_assets_swift(
    cfg: data.StringsI18nConfig,
    *,
    out_path: Path,
    typealias_name: str = "TTAsset",
    enum_name: str = "Asset",
    case_prefix: str = "TT_",
    created_by: str = "flywithbug",
) -> Path:
    all_assets = _build_all_assets(cfg)
    if not all_assets:
        raise data.ConfigError("未在 assets_paths 中发现任何 *.imageset / *.symbolset")

    dups = {k: v for k, v in all_assets.items() if len(v) > 1}
    if dups:
        _print_duplicate_assets(dups)
        if sys.stdin.isatty():
            ans = input("存在同名资源，是否继续生成？(y/N) ").strip().lower()
            if ans != "y":
                raise data.ConfigError("已取消生成（请先处理同名资源）")
        else:
            raise data.ConfigError("存在同名资源，已中止生成")

    items = sorted(all_assets.keys())
    proposed = {n: _sanitize_case_name(n, prefix=case_prefix) for n in items}
    unique = _ensure_unique(proposed)
    today = datetime.datetime.now().strftime("%Y/%m/%d")
    year = datetime.datetime.now().strftime("%Y")

    lines: list[str] = []
    lines.append("//")
    lines.append(f"//  {out_path.name}")
    lines.append("//")
    lines.append(f"//  Auto-generated by gen_tt_image_asset.swift.py on {today}.")
    lines.append("//  Do not edit by hand; re-run the generator instead.")
    lines.append("//")
    lines.append(f"//  Created by {created_by} on {today}.")
    lines.append(f"//  Copyright © {year} {created_by}. All rights reserved.")
    lines.append("//")
    lines.append("")
    lines.append("import UIKit")
    lines.append("")
    lines.append(f"typealias {typealias_name} = UIImage.{enum_name}")
    lines.append("")
    lines.append("extension UIImage {")
    lines.append(f"    enum {enum_name}: String {{")
    for name in items:
        case_name = unique[name]
        lines.append(f"        case {case_name} = \"{name}\"")
    lines.append("")
    lines.append("        var image: UIImage {")
    lines.append("            return UIImage(asset: self)")
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append(f"    convenience init!(asset: {enum_name}) {{")
    lines.append("        self.init(named: asset.rawValue)")
    lines.append("    }")
    lines.append("}")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="box_strings_i18n gen_assets",
        description="Generate Swift asset enum from Assets.xcassets",
    )
    parser.add_argument(
        "--config",
        default=data.DEFAULT_TEMPLATE_NAME,
        help=f"配置文件路径（默认 {data.DEFAULT_TEMPLATE_NAME}，基于 project-root）",
    )
    parser.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument(
        "--assets-out",
        default="TTImageAsset.swift",
        help="输出 Swift 文件路径（默认写到 project_root 下；相对路径按 project_root）",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    cfg_path = (project_root / args.config).resolve()

    try:
        data.assert_config_ok(cfg_path, project_root=project_root, check_paths_exist=True)
    except data.ConfigError as e:
        print(str(e))
        return 1

    try:
        cfg = data.load_config(cfg_path, project_root=project_root)
    except Exception as e:
        print(f"❌ 配置加载失败：{e}")
        return 1

    out_arg = Path(args.assets_out)
    out_path = out_arg if out_arg.is_absolute() else (project_root / out_arg).resolve()

    try:
        fp = generate_assets_swift(cfg, out_path=out_path)
        print(f"✅ 已生成：{fp}")
        return 0
    except Exception as e:
        print(f"❌ 生成失败：{e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

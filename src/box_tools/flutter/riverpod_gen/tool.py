#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _share.tool_spec import tool, opt, ex 

BOX_TOOL = tool(
    id="flutter.riverpod_gen",
    name="box_riverpod_gen",  # ✅ 命令名统一加 box_ 前缀
    category="flutter",
    summary="生成 Riverpod StateNotifier + State 模板文件（notifier/state）",
    usage=[
        "box_riverpod_gen",
        "box_riverpod_gen Product",
        "box_riverpod_gen product_item --out lib/features/product",
        "box_riverpod_gen Product --force",
        "box_riverpod_gen Product --no-copywith",
        "box_riverpod_gen Product --legacy",
    ],
    options=[
        opt("--out", "输出目录（默认当前目录）"),
        opt("--force", "覆盖已存在文件"),
        opt("--no-copywith", "不生成 copy_with_extension 注解与 part '*.g.dart'"),
        opt("--legacy", "notifier 使用 flutter_riverpod/legacy.dart（默认启用 legacy）"),
        opt("--modern", "notifier 使用 flutter_riverpod/flutter_riverpod.dart"),
    ],
    examples=[
        ex("box_riverpod_gen", "交互输入类名与输出目录"),
        ex("box_riverpod_gen Product", "在当前目录生成 product_notifier.dart 与 product_state.c.dart"),
        ex("box_riverpod_gen product_item --out lib/features/product", "在指定目录生成 product_item_* 文件"),
        ex("box_riverpod_gen Product --force", "覆盖已存在文件"),
    ],
    docs="README.md",  # ✅ 约定：docs 永远写 README.md（相对工具目录）
)


_CAMEL_SPLIT_RE = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(s: str) -> str:
    # ProductItem -> product_item
    return _CAMEL_SPLIT_RE.sub("_", s).lower()


def snake_to_pascal(s: str) -> str:
    # product_item -> ProductItem
    return "".join(word[:1].upper() + word[1:] for word in s.split("_") if word)


def normalize_to_pascal(name: str) -> str:
    """Accepts: Product, product, product_item, ProductItem -> Product / ProductItem"""
    name = name.strip()
    if not name:
        raise ValueError("名称不能为空")

    # snake_case -> PascalCase
    if "_" in name:
        base = re.sub(r"_+", "_", name.lower()).strip("_")
        pascal = snake_to_pascal(base)
    else:
        # camel/pascal/single word
        pascal = name[:1].upper() + name[1:]

    if not re.match(r"^[A-Z][A-Za-z0-9]*$", pascal):
        raise ValueError(f"无法解析为有效 Dart 类名: {name!r}")
    return pascal


def lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_file(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"文件已存在: {path}（使用 --force 覆盖）")
    path.write_text(content, encoding="utf-8")


def build_notifier_content(
        class_name: str,
        file_base: str,
        lower_class: str,
        use_legacy: bool,
) -> str:
    import_line = (
        "import 'package:flutter_riverpod/legacy.dart';"
        if use_legacy
        else "import 'package:flutter_riverpod/flutter_riverpod.dart';"
    )

    return (
        f"{import_line}\n\n"
        f"import '{file_base}_state.c.dart';\n\n"
        f"final {lower_class}Provider = StateNotifierProvider.autoDispose<{class_name}Notifier,\n"
        f"    {class_name}State>(\n"
        f"  (ref) => {class_name}NotifierImpl(),\n"
        f");\n\n"
        f"abstract class {class_name}Notifier extends StateNotifier<{class_name}State> {{\n"
        f"  {class_name}Notifier(super.state);\n"
        f"}}\n\n"
        f"class {class_name}NotifierImpl extends {class_name}Notifier {{\n"
        f"  {class_name}NotifierImpl() : super({class_name}State.empty());\n"
        f"}}\n"
    )


def build_state_content(class_name: str, file_base: str, with_copywith: bool) -> str:
    if with_copywith:
        return (
            "import 'package:copy_with_extension/copy_with_extension.dart';\n\n"
            f"part '{file_base}_state.c.g.dart';\n\n"
            "@CopyWith()\n"
            f"class {class_name}State {{\n"
            f"  const {class_name}State({{\n"
            "    this.isLoading = false,\n"
            "  });\n\n"
            f"  factory {class_name}State.empty() {{\n"
            f"    return const {class_name}State();\n"
            "  }\n\n"
            "  final bool isLoading;\n"
            "}\n"
        )

    return (
        f"class {class_name}State {{\n"
        f"  const {class_name}State({{\n"
        "    this.isLoading = false,\n"
        "  });\n\n"
        f"  factory {class_name}State.empty() {{\n"
        f"    return const {class_name}State();\n"
        "  }\n\n"
        "  final bool isLoading;\n"
        "}\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=BOX_TOOL["name"],
        description=BOX_TOOL["summary"],
    )
    p.add_argument(
        "name",
        nargs="?",
        help="类名（支持 Product / product / product_item / ProductItem；不填则交互输入）",
    )
    p.add_argument("--out", default=".", help="输出目录（默认当前目录）")
    p.add_argument("--force", action="store_true", help="覆盖已存在文件")
    p.add_argument(
        "--no-copywith",
        action="store_true",
        help="不生成 copy_with_extension 注解与 part '*.g.dart'",
    )

    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--legacy",
        action="store_true",
        help="使用 flutter_riverpod/legacy.dart（默认）",
    )
    g.add_argument(
        "--modern",
        action="store_true",
        help="使用 flutter_riverpod/flutter_riverpod.dart",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    raw_name = args.name
    if not raw_name:
        raw_name = input("Enter the class name (e.g., Product): ").strip()

    try:
        class_name = normalize_to_pascal(raw_name)
    except Exception as e:
        print(f"❌ 名称解析失败: {e}")
        return 2

    file_base = camel_to_snake(class_name)
    lower_class = lower_first(class_name)

    out_dir = Path(args.out).expanduser().resolve()
    ensure_dir(out_dir)

    # ✅ 默认 legacy，除非显式 modern
    use_legacy = True
    if args.modern:
        use_legacy = False
    elif args.legacy:
        use_legacy = True

    with_copywith = not args.no_copywith

    notifier_path = out_dir / f"{file_base}_notifier.dart"
    state_path = out_dir / f"{file_base}_state.c.dart"

    notifier_content = build_notifier_content(class_name, file_base, lower_class, use_legacy)
    state_content = build_state_content(class_name, file_base, with_copywith)

    try:
        write_file(notifier_path, notifier_content, args.force)
        write_file(state_path, state_content, args.force)
    except FileExistsError as e:
        print(f"❌ {e}")
        return 1

    print("✅ Generated files:")
    print(f"- {notifier_path}")
    print(f"- {state_path}")

    if with_copywith:
        print("ℹ️ 记得执行 build_runner 生成 *.g.dart（如 flutter pub run build_runner build -d）")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl+C：优雅退出，不打印 traceback
        print("\n已取消。")
        raise SystemExit(130)  # 130 = SIGINT 的惯例退出码

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


from box_tools._share.openai_translate.models import OpenAIModel
from box_tools._share.openai_translate.translate_file import translate_from_to, FileProgress

from _share.tool_spec import tool, opt, ex

BOX_TOOL = tool(
    id="ai.file",
    name="box_ai_file",
    category="ai",
    summary="交互式多语言翻译：选择源语言/目标语言后输入文本，AI 实时翻译（支持中途切换）",
    usage=[
        "box_ai_file",
        "box_ai_file --model gpt-4o-mini",
        "box_ai_file --source en --target zh-Hant --in en.json --out zh-hant.json",
        "box_ai_file --in Base.lproj/Localizable.strings --out zh-Hant.lproj/Localizable.strings",
    ],
    options=[
        opt("--model", "指定模型（默认 gpt-4o-mini）"),
        opt("--api-key", "显式传入 OpenAI API Key（不传则读取 OPENAI_API_KEY）"),
        opt("--source", "源语言代码（如 en/zh-Hans/ja…；不传则交互选择）"),
        opt("--target", "目标语言代码（如 zh-Hant/ko/fr…；不传则交互选择）"),
        opt("--in", "源文件路径（支持相对路径；不传则交互输入）"),
        opt("--out", "目标文件路径（支持相对路径；不传则交互输入）"),
        opt("--batch-size", "每批翻译条数（默认 40）"),
        opt("--no-pre-sort", "翻译前不做排序（默认会对源/目标做排序以稳定输出）"),
    ],
    examples=[
        ex("export OPENAI_API_KEY='sk-***' && box_ai_file", "交互选择语言并输入文件路径"),
        ex("box_ai_file --source en --target zh-Hant --in en.json --out zh-hant.json", "英->繁中，翻译 json 文件"),
        ex("box_ai_file --in Base.lproj/Localizable.strings --out zh-Hant.lproj/Localizable.strings", "翻译 iOS .strings 文件"),
    ],
    dependencies=[
        "openai>=1.0.0",
    ],
    docs="README.md",
)

# 你给的那套语言集合（可按需扩展）
LANG_CHOICES: List[Tuple[str, str]] = [
    ("en", "英语 (English)"),
    ("zh-Hans", "中文简体 (简体中文)"),
    ("zh-Hant", "繁体中文 (繁體中文)"),
    ("yue", "粤语 (廣東話)"),
    ("ja", "日语 (日本語)"),
    ("ko", "韩语 (한국어)"),
    ("fr", "法语 (Français)"),
]
LANG_NAME_BY_CODE: Dict[str, str] = {code: name for code, name in LANG_CHOICES}


def _print_lang_menu() -> None:
    print("可选语言：")
    for i, (code, name) in enumerate(LANG_CHOICES, start=1):
        print(f"  {i}. {name}  [{code}]")
    print("")


def _pick_lang(prompt: str) -> str:
    while True:
        _print_lang_menu()
        s = input(prompt).strip()

        if s in LANG_NAME_BY_CODE:
            return s

        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= len(LANG_CHOICES):
                return LANG_CHOICES[idx - 1][0]

        print("输入无效，请输入序号或语言代码。\n")


def _normalize_model(m: str) -> str:
    s = (m or "").strip()
    return s if s else OpenAIModel.GPT_4O_MINI.value


def _abs_path(p: str) -> str:
    # 支持相对路径：相对于当前执行目录
    return str(Path(p).expanduser().resolve())


def _print_help() -> None:
    print(
        "指令：\n"
        "  /help                 帮助\n"
        "  /exit                 退出\n"
        "  /langs                显示语言列表\n"
        "  /source               重新选择源语言\n"
        "  /target               重新选择目标语言\n"
        "  /swap                 交换源语言与目标语言\n"
        "  /show                 显示当前配置\n"
        "  /in                   重新输入源文件路径\n"
        "  /out                  重新输入目标文件路径\n"
        "  /run                  执行翻译\n"
        "\n"
        "用法：按提示选语言与输入文件路径，然后 /run 开始翻译。\n"
    )


@dataclass
class _State:
    src: str
    tgt: str
    in_path: str
    out_path: str
    model: str
    api_key: Optional[str]
    batch_size: int
    pre_sort: bool


def _show_state(st: _State) -> None:
    src_name = LANG_NAME_BY_CODE.get(st.src, st.src)
    tgt_name = LANG_NAME_BY_CODE.get(st.tgt, st.tgt)
    print("当前配置：")
    print(f"  source: {src_name} [{st.src}]")
    print(f"  target: {tgt_name} [{st.tgt}]")
    print(f"  in:     {st.in_path}")
    print(f"  out:    {st.out_path}")
    print(f"  model:  {st.model}")
    print(f"  batch:  {st.batch_size}")
    print(f"  preSort:{st.pre_sort}")
    print("")


def _ask_file(prompt: str) -> str:
    while True:
        p = input(prompt).strip()
        if p:
            return p
        print("路径不能为空。\n")


def _progress_printer(p: FileProgress) -> None:
    # 你后面接 panel 时，就把这层换成更结构化的渲染即可
    msg = f" {p.message}" if p.message else ""
    print(f"[{p.stage}] {p.file} {p.done}/{p.total}{msg}")


def _run_translate(st: _State) -> None:
    src_abs = _abs_path(st.in_path)
    out_abs = _abs_path(st.out_path)

    if not os.path.exists(src_abs):
        raise FileNotFoundError(f"源文件不存在：{src_abs}")

    translate_from_to(
        source_file_path=src_abs,
        target_file_path=out_abs,
        src_locale=st.src,
        tgt_locale=st.tgt,
        model=st.model,
        api_key=st.api_key,
        progress=_progress_printer,
        batch_size=st.batch_size,
        pre_sort=st.pre_sort,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog=BOX_TOOL["name"], add_help=True)
    parser.add_argument("--model", default=OpenAIModel.GPT_4O_MINI.value, help="模型名，如 gpt-4o-mini")
    parser.add_argument("--api-key", default=None, help="可选：显式传入 OpenAI API key（不传则读 OPENAI_API_KEY）")
    parser.add_argument("--source", default=None, help="源语言代码（如 en/zh-Hans/ja…；不传则交互选择）")
    parser.add_argument("--target", default=None, help="目标语言代码（如 zh-Hant/ko/fr…；不传则交互选择）")
    parser.add_argument("--in", dest="in_path", default=None, help="源文件路径（相对/绝对）")
    parser.add_argument("--out", dest="out_path", default=None, help="目标文件路径（相对/绝对）")
    parser.add_argument("--batch-size", type=int, default=40, help="每批翻译条数（默认 40）")
    parser.add_argument("--no-pre-sort", action="store_true", help="翻译前不排序（默认排序）")
    args = parser.parse_args(argv)

    model_name = _normalize_model(args.model)

    # source/target
    src = (args.source or "").strip()
    tgt = (args.target or "").strip()

    if not src:
        print("请选择源语言（source）：")
        src = _pick_lang("source > ")

    if not tgt:
        print("请选择目标语言（target）：")
        tgt = _pick_lang("target > ")

    # files
    in_path = (args.in_path or "").strip()
    out_path = (args.out_path or "").strip()

    if not in_path:
        print("请输入源文件路径（支持相对路径，如 ./en.json）：")
        in_path = _ask_file("in > ")

    if not out_path:
        print("请输入目标文件路径（支持相对路径，如 ./zh-hant.json）：")
        out_path = _ask_file("out > ")

    st = _State(
        src=src,
        tgt=tgt,
        in_path=in_path,
        out_path=out_path,
        model=model_name,
        api_key=args.api_key,
        batch_size=max(1, int(args.batch_size)),
        pre_sort=not args.no_pre_sort,
    )

    print("\n进入文件翻译模式：输入 /help 查看指令。\n")
    _show_state(st)

    while True:
        try:
            cmd = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0

        if not cmd:
            continue

        if not cmd.startswith("/"):
            print("请输入指令（/run 开始翻译，/help 查看帮助）。\n")
            continue

        if cmd in ("/exit", "/quit"):
            print("退出。")
            return 0

        if cmd == "/help":
            _print_help()
            continue

        if cmd == "/langs":
            _print_lang_menu()
            continue

        if cmd == "/show":
            _show_state(st)
            continue

        if cmd == "/swap":
            st.src, st.tgt = st.tgt, st.src
            print("已交换 source/target。\n")
            _show_state(st)
            continue

        if cmd == "/source":
            print("重新选择源语言（source）：")
            st.src = _pick_lang("source > ")
            _show_state(st)
            continue

        if cmd == "/target":
            print("重新选择目标语言（target）：")
            st.tgt = _pick_lang("target > ")
            _show_state(st)
            continue

        if cmd == "/in":
            print("重新输入源文件路径：")
            st.in_path = _ask_file("in > ")
            _show_state(st)
            continue

        if cmd == "/out":
            print("重新输入目标文件路径：")
            st.out_path = _ask_file("out > ")
            _show_state(st)
            continue

        if cmd == "/run":
            try:
                print("开始翻译...\n")
                _run_translate(st)
                print("\n完成。\n")
            except Exception as e:
                print(f"\n[错误] {e}\n")
            continue

        print("未知指令：/help 查看可用指令。\n")


if __name__ == "__main__":
    raise SystemExit(main())

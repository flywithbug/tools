from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from box_tools._share.openai_translate.models import OpenAIModel
from box_tools._share.openai_translate.translate import translate_flat_dict


BOX_TOOL = {
    "id": "ai.translate",
    "name": "box_ai_translate",
    "category": "ai",
    "summary": "交互式多语言翻译：选择源语言/目标语言后输入文本，AI 实时翻译（支持中途切换）",
    "usage": [
        "box_ai_translate",
        "box_ai_translate --model gpt-4o-mini",
        "box_ai_translate --source en --target zh-Hans",
    ],
    "options": [
        {"flag": "--model", "desc": "指定模型（默认 gpt-4o-mini）"},
        {"flag": "--api-key", "desc": "显式传入 OpenAI API Key（不传则读取 OPENAI_API_KEY）"},
        {"flag": "--source", "desc": "源语言代码（如 en/zh-Hans/ja…；不传则交互选择）"},
        {"flag": "--target", "desc": "目标语言代码（如 zh-Hant/ko/fr…；不传则交互选择）"},
    ],
    "examples": [
        {"cmd": "export OPENAI_API_KEY='sk-***' && box_ai_translate", "desc": "进入翻译模式并交互选择语言"},
        {"cmd": "box_ai_translate --source en --target zh-Hans", "desc": "跳过选项表，直接英->简中"},
    ],
    "dependencies": [
        "PyYAML>=6.0",
        "openai>=1.0.0",
    ],
    "docs": "README.md",
}


# 你要求的语言集合：英语/简中/繁中/粤语/日语/韩语/法语
# 说明：
# - zh-Hans: 简体中文（推荐用法）
# - zh-Hant: 繁体中文（推荐用法）
# - yue: 粤语（常用 BCP47/ISO 639-3）
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

        # 允许直接输入 code，比如 en/zh-Hans/ja...
        if s in LANG_NAME_BY_CODE:
            return s

        # 允许输入序号
        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= len(LANG_CHOICES):
                return LANG_CHOICES[idx - 1][0]

        print("输入无效，请输入序号(1-7)或语言代码（如 en / zh-Hans / ja）。\n")


def _normalize_model(m: str) -> str:
    s = (m or "").strip()
    return s if s else OpenAIModel.GPT_4O_MINI.value


@dataclass
class _State:
    src: str
    tgt: str


def _print_help() -> None:
    print(
        "指令：\n"
        "  /help                 帮助\n"
        "  /exit                 退出\n"
        "  /langs                显示语言列表\n"
        "  /source               重新选择源语言\n"
        "  /target               重新选择目标语言\n"
        "  /swap                 交换源语言与目标语言\n"
        "  /show                 显示当前 source/target\n"
        "\n"
        "用法：直接输入任意文本即可翻译。\n"
    )


def _show_state(st: _State) -> None:
    src_name = LANG_NAME_BY_CODE.get(st.src, st.src)
    tgt_name = LANG_NAME_BY_CODE.get(st.tgt, st.tgt)
    print(f"当前：source={src_name} [{st.src}]  ->  target={tgt_name} [{st.tgt}]\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog=BOX_TOOL["name"], add_help=True)
    parser.add_argument("--model", default=OpenAIModel.GPT_4O_MINI.value, help="模型名，如 gpt-4o-mini")
    parser.add_argument("--api-key", default=None, help="可选：显式传入 OpenAI API key（不传则读 OPENAI_API_KEY）")
    parser.add_argument("--source", default=None, help="源语言代码（如 en/zh-Hans/ja…；不传则交互选择）")
    parser.add_argument("--target", default=None, help="目标语言代码（如 zh-Hant/ko/fr…；不传则交互选择）")
    args = parser.parse_args(argv)

    model_name = _normalize_model(args.model)

    # 选择 source/target
    src = (args.source or "").strip()
    tgt = (args.target or "").strip()

    if not src:
        print("请选择源语言（source）：")
        src = _pick_lang("source > ")

    if not tgt:
        print("请选择目标语言（target）：")
        tgt = _pick_lang("target > ")

    st = _State(src=src, tgt=tgt)

    print("\n进入翻译模式：直接输入文本即可翻译。输入 /help 查看指令。\n")
    _show_state(st)

    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0

        if not text:
            continue

        # commands
        if text.startswith("/"):
            cmd = text.strip()

            if cmd == "/help":
                _print_help()
                continue

            if cmd in ("/exit", "/quit"):
                print("退出。")
                return 0

            if cmd == "/langs":
                _print_lang_menu()
                continue

            if cmd == "/show":
                _show_state(st)
                continue

            if cmd == "/swap":
                st.src, st.tgt = st.tgt, st.src
                print("已交换 source/target。")
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

            print("未知指令：/help 查看可用指令。\n")
            continue

        # translate
        try:
            # 用 translate_flat_dict 复用你已有的翻译底座能力
            out = translate_flat_dict(
                prompt_en=None,
                src_dict={"text": text},
                src_lang=st.src,
                tgt_locale=st.tgt,
                model=model_name,
                api_key=args.api_key,  # 不传则内部自动读 OPENAI_API_KEY，并在缺失时提示 export 方法
            )
            print(out.get("text", ""))
        except Exception as e:
            print(f"[错误] {e}")

    # unreachable
    # return 0


if __name__ == "__main__":
    raise SystemExit(main())

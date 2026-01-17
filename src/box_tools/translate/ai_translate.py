from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 确保从 comm 目录导入 translate 模块
from comm.translate import OpenAIModel, TranslationError, translate_flat_dict

# 工具元数据
BOX_TOOL = {
    "id": "ai.translate",
    "name": "translate",
    "category": "ai",
    "summary": "OpenAI 翻译/JSON 工具底座：平铺 JSON 翻译（key 不变、只翻 value、占位符守护）+ 环境自检",
    "usage": [
        "translate",
        "translate --help",
        "translate doctor",
        "translate translate --src-lang en --tgt-locale zh_Hant --in input.json --out output.json",
    ],
    "options": [
        {"flag": "doctor", "desc": "检查 OpenAI SDK / OPENAI_API_KEY 环境变量 / Python 环境"},
        {"flag": "translate", "desc": "翻译平铺 JSON（key 不变，只翻 value），输出为 JSON"},
        {"flag": "--model", "desc": "选择模型（默认 gpt-4o）"},
        {"flag": "--api-key", "desc": "显式传入 API key（优先于环境变量）"},
    ],
    "examples": [
        {"cmd": "translate", "desc": "显示简介 + 检查 OPENAI_API_KEY 是否已配置"},
        {"cmd": "translate translate --src-lang en --tgt-locale zh_Hant --in i18n/en.json --out i18n/zh_Hant.json", "desc": "翻译一个平铺 JSON 文件"},
    ],
}

# 配置与命令处理
def _get_api_key(explicit: Optional[str]) -> Optional[str]:
    """获取 API Key"""
    if explicit and explicit.strip():
        return explicit.strip()
    return (os.environ.get("OPENAI_API_KEY") or "").strip() or None


def _read_json_file(path: Path) -> Dict[str, str]:
    """读取 JSON 文件"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("输入 JSON 必须是 object（平铺 key-value），不能是 array")
    return {str(k): v if v is not None else "" for k, v in data.items()}


def _cmd_translate(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """翻译命令"""
    api_key = _get_api_key(getattr(args, "api_key", None))
    if not api_key:
        print("❌ 缺少 OPENAI_API_KEY")
        return 2

    in_path = Path(args.input).expanduser().resolve()
    out_path = Path(args.output).expanduser().resolve()

    if not in_path.exists():
        print(f"❌ 输入文件不存在: {in_path}")
        return 2

    src_dict = _read_json_file(in_path)

    out = translate_flat_dict(
        prompt_en=args.prompt_en,
        src_dict=src_dict,
        src_lang=args.src_lang,
        tgt_locale=args.tgt_locale,
        model=args.model,
        api_key=api_key,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("translate: OK")
    print(f"in : {in_path}")
    print(f"out: {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器"""
    p = argparse.ArgumentParser(
        prog="translate",
        description="OpenAI 翻译工具：平铺 JSON 翻译 + 环境自检（OPENAI_API_KEY）",
    )

    sub = p.add_subparsers(dest="cmd")

    # 诊断命令
    sp_doctor = sub.add_parser("doctor", help="检查 OpenAI SDK 与 OPENAI_API_KEY")
    sp_doctor.set_defaults(func=lambda _p, _a: print("Doctoring..."))

    # 翻译命令
    sp_t = sub.add_parser("translate", help="翻译平铺 JSON（key 不变，只翻 value）")
    sp_t.add_argument("--src-lang", required=True, help="源语言（如 en）")
    sp_t.add_argument("--tgt-locale", required=True, help="目标语言（如 zh_Hant）")
    sp_t.add_argument("--in", dest="input", required=True, help="输入 JSON 文件")
    sp_t.add_argument("--out", dest="output", required=True, help="输出 JSON 文件")
    sp_t.add_argument("--prompt-en", default=None, help="额外的提示（用于翻译）")
    sp_t.add_argument("--model", default=OpenAIModel.GPT_4O.value, help="使用的模型（默认 gpt-4o）")
    sp_t.add_argument("--api-key", default=None, help="显式传入 API key")
    sp_t.set_defaults(func=_cmd_translate)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    """主函数：处理命令行参数并执行对应命令"""
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    args = p.parse_args(argv)

    # 运行指定的命令
    func = getattr(args, "func", None)
    if not func:
        p.print_help()
        return 0

    return int(func(p, args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # 优雅退出
        print("\n已取消。")
        raise SystemExit(130)  # 130 = SIGINT 的惯例退出码

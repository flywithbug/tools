from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


# =========================================================
# Tool metadata (for box tools listing / README generator)
# =========================================================

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
        "translate translate --src-lang en --tgt-locale ja --in input.json --out output.json --prompt-en 'Use polite tone'",
    ],
    "options": [
        {"flag": "doctor", "desc": "检查 OpenAI SDK / OPENAI_API_KEY 环境变量 / Python 环境"},
        {"flag": "translate", "desc": "翻译平铺 JSON（key 不变，只翻 value），输出为 JSON"},
        {"flag": "--model", "desc": "选择模型（默认 gpt-4o）"},
        {"flag": "--api-key", "desc": "显式传入 API key（优先于环境变量）"},
    ],
    "examples": [
        {"cmd": "translate", "desc": "显示简介 + 检查 OPENAI_API_KEY 是否已配置"},
        {"cmd": "translate doctor", "desc": "更详细的环境自检"},
        {"cmd": "translate translate --src-lang en --tgt-locale zh_Hant --in i18n/en.json --out i18n/zh_Hant.json", "desc": "翻译一个平铺 JSON 文件"},
    ],
    "docs": "src/box/gpt.md",
}


# =========================================================
# Models (Enum)
# =========================================================


class OpenAIModel(str, Enum):
    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"
    GPT_4_1_NANO = "gpt-4.1-nano"
    O3_MINI = "o3-mini"


# =========================================================
# Errors
# =========================================================


class TranslationError(RuntimeError):
    pass


# =========================================================
# Internal fixed options
# =========================================================


@dataclass(frozen=True)
class _Options:
    context_limit: int = 16000
    safety_ratio: float = 0.70
    max_chunk_items: int = 250

    timeout: float = 30.0
    retries: int = 2
    temperature: float = 0.0
    top_p: float = 1.0
    backoff_base: float = 1.6
    backoff_jitter: float = 0.25

    strict_key_match: bool = True
    prefer_response_format_json: bool = True

    placeholder_guard: bool = True


# =========================================================
# Token estimation (best-effort)
# =========================================================


class _TokenEstimator:
    def __init__(self, model: str):
        self._enc = None
        try:
            import tiktoken  # type: ignore

            try:
                self._enc = tiktoken.encoding_for_model(model)
            except Exception:
                self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            pass

    def count(self, s: str) -> int:
        if not s:
            return 0
        if self._enc:
            return len(self._enc.encode(s))
        return int(len(s) / 4) + 8


# =========================================================
# Placeholder protection (single built-in regex)
# =========================================================


_PLACEHOLDER_RE = re.compile(
    r"(?:"
    r"{{\s*[A-Za-z0-9_.-]+\s*}}"
    r"|\$\{\s*[A-Za-z0-9_.-]+\s*\}"
    r"|%\{\s*[A-Za-z0-9_.-]+\s*\}"
    r"|%\(\s*[A-Za-z0-9_.-]+\s*\)[a-zA-Z]"
    r"|%(?:\d+\$)?[#0\- +']*\d*(?:\.\d+)?[a-zA-Z@]"
    r"|%%"
    r"|{[^{}]+}"
    r")"
)

_PLACEHOLDER_EXAMPLES = (
    "{name}, {0}, {{name}}, ${name}, %{name}, %(name)s, %1$s, %@, %.2f, %%"
)


def _extract_placeholders(text: str) -> List[str]:
    return [m.group(0) for m in _PLACEHOLDER_RE.finditer(text or "")]


def _multiset(xs: List[str]) -> Dict[str, int]:
    d: Dict[str, int] = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return d


def _placeholders_compatible(src: str, tgt: str) -> bool:
    return _multiset(_extract_placeholders(src)) == _multiset(_extract_placeholders(tgt))


def _guard_placeholders(src: Dict[str, str], out: Dict[str, str]) -> Dict[str, str]:
    fixed: Dict[str, str] = {}
    for k, src_text in src.items():
        tgt_text = out.get(k, "")
        src_ph = _extract_placeholders(src_text)

        if not src_ph or _placeholders_compatible(src_text, tgt_text):
            fixed[k] = tgt_text
            continue

        it = iter(src_ph)
        fixed[k] = _PLACEHOLDER_RE.sub(lambda m: next(it, m.group(0)), tgt_text)

    return fixed


# =========================================================
# Prompt builder (default prompt + extra)
# =========================================================


def _build_system_prompt(
    *,
    src_lang: str,
    tgt_locale: str,
    prompt_en: Optional[str],
) -> str:
    base = (
        "You are a professional localization translator for apps and web. "
        f"Translate from {src_lang} to {tgt_locale}. "
        "Preserve brand names and URLs verbatim. "
        f"Preserve ALL placeholders and formatting tokens EXACTLY as-is (e.g., {_PLACEHOLDER_EXAMPLES}). "
        "Return ONLY a single valid JSON object. "
        "The JSON keys MUST match the input keys exactly; translate ONLY the values. "
        "No extra commentary. No markdown. No code fences. "
        "The input JSON is a flat object mapping keys to strings. Output a JSON object with the SAME keys ONLY."
    )

    extra = (prompt_en or "").strip()
    return base if not extra else f"{base} {extra}"


def _build_user_payload(_tgt_locale: str, chunk: Dict[str, str]) -> str:
    # Do NOT wrap with {tgt_locale, payload}. tgt_locale is already conveyed in system prompt.
    return json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))


# =========================================================
# Chunking
# =========================================================


def _chunk_flat_dict(
    src: Dict[str, str],
    estimator: _TokenEstimator,
    system_prompt: str,
    tgt_locale: str,
    opt: _Options,
) -> List[Dict[str, str]]:
    if not src:
        return []

    budget = int(opt.context_limit * opt.safety_ratio)
    sys_cost = estimator.count(system_prompt)

    def cost(d: Dict[str, str]) -> int:
        return sys_cost + estimator.count(_build_user_payload(tgt_locale, d)) + 16

    chunks: List[Dict[str, str]] = []
    cur: Dict[str, str] = {}

    for k, v in src.items():
        if not cur:
            cur = {k: v}
            if cost(cur) > budget:
                chunks.append(cur)
                cur = {}
            continue

        cand = dict(cur)
        cand[k] = v

        if cost(cand) > budget or len(cand) > opt.max_chunk_items:
            chunks.append(cur)
            cur = {k: v}
        else:
            cur = cand

    if cur:
        chunks.append(cur)

    return chunks


# =========================================================
# OpenAI helpers
# =========================================================


def _sleep_backoff(attempt: int, base: float, jitter: float) -> None:
    time.sleep((base**attempt) + random.uniform(0, jitter))


def _parse_json_object(text: str) -> Dict[str, str]:
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Model output is not a JSON object")
    return {str(k): "" if v is None else str(v) for k, v in obj.items()}


def _validate_keys(expected: Iterable[str], got: Dict[str, str], strict: bool) -> None:
    exp = set(expected)
    got_set = set(got.keys())
    if strict and exp != got_set:
        raise ValueError(
            f"Key mismatch: missing={list(exp-got_set)[:5]} extra={list(got_set-exp)[:5]}"
        )
    if not strict:
        missing = exp - got_set
        if missing:
            raise ValueError(f"Missing keys: {list(missing)[:5]}")


def _chat_completion(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    opt: _Options,
    use_json_format: bool,
) -> str:
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=opt.temperature,
        top_p=opt.top_p,
    )
    if use_json_format and opt.prefer_response_format_json:
        kwargs["response_format"] = {"type": "json_object"}

    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


# =========================================================
# Public API
# =========================================================


def translate_flat_dict(
    *,
    prompt_en: Optional[str],
    src_dict: Dict[str, str],
    src_lang: str,
    tgt_locale: str,
    model: Optional[str] = None,
    api_key: str,
) -> Dict[str, str]:
    """Translate a flat dict: {key: text} -> {key: translated_text}."""

    if not OpenAI:
        raise SystemExit("OpenAI SDK 未安装，请先 pip install openai>=1.0.0")

    opt = _Options()

    model_name = (
        OpenAIModel.GPT_4O.value
        if model is None
        else (model.value if isinstance(model, Enum) else str(model))
    )

    system_prompt = _build_system_prompt(
        src_lang=src_lang,
        tgt_locale=tgt_locale,
        prompt_en=prompt_en,
    )

    estimator = _TokenEstimator(model_name)
    client = OpenAI(api_key=api_key, timeout=opt.timeout)

    chunks = _chunk_flat_dict(src_dict, estimator, system_prompt, tgt_locale, opt)
    merged: Dict[str, str] = {}

    def translate_chunk(chunk: Dict[str, str]) -> Dict[str, str]:
        user_content = _build_user_payload(tgt_locale, chunk)
        last_err: Optional[Exception] = None
        use_json_format = True

        for attempt in range(opt.retries + 1):
            try:
                out_text = _chat_completion(
                    client,
                    model_name,
                    system_prompt,
                    user_content,
                    opt,
                    use_json_format,
                )
                out = _parse_json_object(out_text)
                _validate_keys(chunk.keys(), out, opt.strict_key_match)

                if opt.placeholder_guard:
                    out = _guard_placeholders(chunk, out)

                return out

            except Exception as e:
                last_err = e
                msg = str(e).lower()

                # Some older endpoints/models may not support response_format.
                if use_json_format and "response_format" in msg:
                    use_json_format = False
                    continue

                if attempt < opt.retries:
                    _sleep_backoff(attempt, opt.backoff_base, opt.backoff_jitter)

        if len(chunk) == 1:
            raise TranslationError(f"Chunk failed permanently: {last_err}")

        items = list(chunk.items())
        mid = len(items) // 2
        left = dict(items[:mid])
        right = dict(items[mid:])
        out = translate_chunk(left)
        out.update(translate_chunk(right))
        return out

    for ch in chunks:
        merged.update(translate_chunk(ch))

    if set(merged.keys()) != set(src_dict.keys()):
        raise TranslationError("Final key mismatch")

    return merged


# =========================================================
# CLI
# =========================================================


def _print_intro() -> None:
    print("== translate ==")
    print("这是工具集里的 OpenAI 小底座：")
    print("- 平铺 JSON 翻译（key 不变，只翻 value）")
    print("- 占位符/格式化 token 守护（避免 {name} / %s / ${x} 被翻坏）")
    print("- 支持分块与重试（大 JSON 也能稳一点）")
    print()


def _print_api_key_help() -> None:
    print("未检测到 OPENAI_API_KEY。你可以这样配置：")
    print()
    print("macOS / Linux (bash/zsh)：")
    print("  export OPENAI_API_KEY=\"sk-...\"")
    print("  # 你也可以把它写进 ~/.zshrc 或 ~/.bashrc")
    print()
    print("Windows PowerShell：")
    print("  setx OPENAI_API_KEY \"sk-...\"")
    print()
    print("临时传入（优先级最高）：")
    print("  translate translate ... --api-key sk-...")
    print()


def _get_api_key(explicit: Optional[str]) -> Optional[str]:
    if explicit and explicit.strip():
        return explicit.strip()
    return (os.environ.get("OPENAI_API_KEY") or "").strip() or None


def _cmd_doctor(_parser: argparse.ArgumentParser, _args: argparse.Namespace) -> int:
    print("== translate doctor ==")
    print(f"python: {sys.executable}")
    print(f"python_version: {sys.version.split()[0]}")

    if OpenAI is None:
        print("openai_sdk: NOT INSTALLED")
        print("  fix: pip install openai>=1.0.0")
    else:
        try:
            import openai  # type: ignore

            ver = getattr(openai, "__version__", "unknown")
        except Exception:
            ver = "unknown"
        print(f"openai_sdk: OK ({ver})")

    key = _get_api_key(None)
    if key:
        print("OPENAI_API_KEY: OK (已配置)")
    else:
        print("OPENAI_API_KEY: MISSING")
        _print_api_key_help()

    print("doctor: OK")
    return 0


def _read_json_file(path: Path) -> Dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("输入 JSON 必须是 object（平铺 key-value），不能是 array")
    out: Dict[str, str] = {}
    for k, v in data.items():
        out[str(k)] = "" if v is None else str(v)
    return out


def _cmd_translate(_parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    api_key = _get_api_key(getattr(args, "api_key", None))
    if not api_key:
        print("❌ 缺少 OPENAI_API_KEY")
        _print_api_key_help()
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
    p = argparse.ArgumentParser(
        prog="gpt",
        description="OpenAI 小工具：平铺 JSON 翻译 + 环境自检（OPENAI_API_KEY）",
    )

    sub = p.add_subparsers(dest="cmd")

    sp_doctor = sub.add_parser("doctor", help="检查 OpenAI SDK 与 OPENAI_API_KEY")
    sp_doctor.set_defaults(func=_cmd_doctor)

    sp_t = sub.add_parser("translate", help="翻译平铺 JSON（key 不变，只翻 value）")
    sp_t.add_argument("--src-lang", required=True, help="源语言（如 en）")
    sp_t.add_argument("--tgt-locale", required=True, help="目标语言/地区（如 zh_Hant / ja / fr）")
    sp_t.add_argument("--in", dest="input", required=True, help="输入 JSON 文件")
    sp_t.add_argument("--out", dest="output", required=True, help="输出 JSON 文件")
    sp_t.add_argument("--prompt-en", default=None, help="额外英文提示词（追加到 system prompt）")
    sp_t.add_argument(
        "--model",
        default=OpenAIModel.GPT_4O.value,
        help="模型名（默认 gpt-4o）",
    )
    sp_t.add_argument(
        "--api-key",
        default=None,
        help="显式传入 API key（优先于 OPENAI_API_KEY）",
    )
    sp_t.set_defaults(func=_cmd_translate)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Running without args -> intro + env hint (and show help summary)
    if not argv:
        _print_intro()
        if _get_api_key(None):
            print("OPENAI_API_KEY: OK (已配置)")
        else:
            print("OPENAI_API_KEY: MISSING")
            _print_api_key_help()

        print("常用命令：")
        print("  translate doctor")
        print("  translate translate --src-lang en --tgt-locale zh_Hant --in input.json --out output.json")
        return 0

    p = build_parser()
    args = p.parse_args(argv)

    # `translate --help` should behave normally
    if not getattr(args, "cmd", None):
        # No subcommand, but args present (e.g. --help already handled by argparse)
        p.print_help()
        return 0

    func = getattr(args, "func", None)
    if not func:
        p.print_help()
        return 0

    return int(func(p, args))


__all__ = ["OpenAIModel", "TranslationError", "translate_flat_dict", "main", "BOX_TOOL"]

#
# if __name__ == "__main__":
#     try:
#         raise SystemExit(main())
#     except KeyboardInterrupt:
#         # Ctrl+C：优雅退出，不打印 traceback
#         print("\n已取消。")
#         raise SystemExit(130)  # 130 = SIGINT 的惯例退出码
#

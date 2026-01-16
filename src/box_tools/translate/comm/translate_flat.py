from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


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
# Prompt builder (默认提示词 + 外部追加)
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
        "No extra commentary. No markdown. No code fences."
    )

    extra = (prompt_en or "").strip()
    return base if not extra else f"{base} {extra}"


def _build_user_payload(tgt_locale: str, chunk: Dict[str, str]) -> str:
    return json.dumps(
        {"tgt_locale": tgt_locale, "payload": chunk},
        ensure_ascii=False,
        separators=(",", ":"),
    )


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
    time.sleep((base ** attempt) + random.uniform(0, jitter))


def _parse_json_object(text: str) -> Dict[str, str]:
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Model output is not a JSON object")
    return {str(k): "" if v is None else str(v) for k, v in obj.items()}


def _validate_keys(expected: Iterable[str], got: Dict[str, str], strict: bool) -> None:
    exp = set(expected)
    got_set = set(got.keys())
    if strict and exp != got_set:
        raise ValueError(f"Key mismatch: missing={list(exp-got_set)[:5]} extra={list(got_set-exp)[:5]}")
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
    """
    翻译平铺字典：{key: text} -> {key: translated_text}
    """
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


__all__ = ["OpenAIModel", "TranslationError", "translate_flat_dict"]

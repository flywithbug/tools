from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Iterable, Union, Callable, Any

from .client import OpenAIClientFactory
from .models import OpenAIModel


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
    # Context window (tokens). Maintain via config; API doesn't expose it reliably.
    context_limit: int = 88_000

    # ✅ Input (system + user payload) must not exceed this fraction of context_limit.
    # You asked: "cannot exceed of API token limit" => 0.70
    input_budget_ratio: float = 0.70

    # Additional safety headroom (messages overhead, token counting drift).
    overhead_tokens: int = 64

    # Chunk sizing
    # NOTE: We will chunk by key-count first, per caller requirement.
    max_chunk_items: int = 60

    # Request knobs
    timeout: float = 30.0
    retries: int = 2
    temperature: float = 0.0
    top_p: float = 1.0
    backoff_base: float = 1.6
    backoff_jitter: float = 0.25

    strict_key_match: bool = True
    prefer_response_format_json: bool = True

    placeholder_guard: bool = True


ProgressCallback = Callable[[Dict[str, Any]], None]


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
        # Fallback heuristic
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
    return _multiset(_extract_placeholders(src)) == _multiset(
        _extract_placeholders(tgt)
    )


def _guard_placeholders(src: Dict[str, str], out: Dict[str, str]) -> Dict[str, str]:
    """
    Best-effort guard: if placeholders mismatch, replace matched placeholders in tgt
    with src placeholders in order. (Does not "invent" missing placeholders.)
    """
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
        "Translate UI strings naturally for a mobile UI. "
        "Be concise, clear, and consistent. "
        "Requirements: "
        f"- Output must be written entirely in {tgt_locale}. Do not output any other language. "
        "- Do NOT mix languages in normal words or sentences. "
        "- Translate all human-visible text, including short titles/labels before a colon "
        '(e.g., "Account Disabled:", "Network Error:", "Payment Failed:", "Error:", "Warning:"). '
        "- Do not keep an English title followed by a translated sentence; translate the whole string consistently. "
        "- Do NOT treat generic UI titles in Title Case as brand names; translate them "
        '(e.g., "Account Disabled", "Network Error", "Payment Failed"). '
        "Preserve product/brand names (proper nouns) and URLs verbatim. "
        "Only preserve brand/product names when they are explicit proper nouns (e.g., specific app/product/feature names), "
        "not generic UI messages. "
        f"Preserve ALL placeholders and formatting tokens EXACTLY as-is (e.g., {_PLACEHOLDER_EXAMPLES}). "
        "Keep placeholders/variables unchanged (e.g., {x}, %s, %@, ${var}). "
        "Keep formatting intact (punctuation, line breaks, spacing) while making the wording natural. "
        "A colon ':' is normal punctuation, not a placeholder—translate text on both sides if it is human-visible. "
        "Abbreviations: "
        "- Keep international technical/brand abbreviations that are normally written in English "
        "(e.g., Wi-Fi, GPS, API, URL, OTP, 2FA, iOS, Android, PDF, USD). "
        "- If an English abbreviation is commonly translated into normal words in the target language "
        "(based on real local usage), translate it naturally (e.g., FAQ → localized common wording). "
        "- If unsure, prefer the form native users expect in a mobile UI. "
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
# Chunking (by key-count, per product requirement)
# =========================================================


def _chunk_flat_dict(
    src: Dict[str, str],
    opt: _Options,
) -> List[Dict[str, str]]:
    """
    Chunk by key-count only (stable order). This is intentionally simple:
    - It does NOT estimate tokens.
    - It does NOT split by chars.
    """
    if not src:
        return []

    items = list(src.items())
    n = len(items)
    chunks: List[Dict[str, str]] = []
    step = max(1, int(opt.max_chunk_items))

    for i in range(0, n, step):
        part = dict(items[i : i + step])
        chunks.append(part)

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
            f"Key mismatch: missing={list(exp - got_set)[:5]} extra={list(got_set - exp)[:5]}"
        )

    if not strict:
        missing = exp - got_set
        if missing:
            raise ValueError(f"Missing keys: {list(missing)[:5]}")


def _chat_completion(
    client,
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


def _safe_cb(cb: Optional[ProgressCallback], payload: Dict[str, Any]) -> None:
    if not cb:
        return
    try:
        cb(payload)
    except Exception:
        # Progress callbacks must never affect translation.
        return


# =========================================================
# Public API
# =========================================================


def translate_flat_dict(
    *,
    prompt_en: Optional[str],
    src_dict: Dict[str, str],
    src_lang: str,
    tgt_locale: str,
    model: Optional[Union[OpenAIModel, str]] = None,
    api_key: Optional[str] = None,
    # Optional: allow overriding opt defaults from call sites
    opt: Optional[_Options] = None,
    # Optional: progress callback (MUST NOT affect translation if it fails)
    progress_cb: Optional[ProgressCallback] = None,
) -> Dict[str, str]:
    """Translate a flat dict: {key: text} -> {key: translated_text}."""
    opt = opt or _Options()

    # 统一 model 取值：支持 Enum / str / None
    if model is None:
        model_name = OpenAIModel.GPT_4O.value
    elif isinstance(model, Enum):
        # OpenAIModel is a str Enum
        model_name = model.value  # type: ignore[assignment]
    else:
        model_name = str(model).strip() or OpenAIModel.GPT_4O.value

    system_prompt = _build_system_prompt(
        src_lang=src_lang,
        tgt_locale=tgt_locale,
        prompt_en=prompt_en,
    )

    # Keep estimator (even though key-chunking doesn't use it) for potential future usage.
    _ = _TokenEstimator(model_name)

    # ✅ 用你已经拆好的 client 工具：自动检测 OPENAI_API_KEY，没配就提示 export 方法
    client = OpenAIClientFactory(timeout=opt.timeout).create(api_key=api_key)

    chunks = _chunk_flat_dict(src_dict, opt)

    _safe_cb(
        progress_cb,
        {
            "event": "chunking_done",
            "src_lang": src_lang,
            "tgt_locale": tgt_locale,
            "total_items": len(src_dict),
            "chunks": len(chunks),
            "max_chunk_items": opt.max_chunk_items,
            "model": model_name,
        },
    )

    merged: Dict[str, str] = {}

    def translate_chunk(
        chunk: Dict[str, str], chunk_idx_1: int, chunk_total: int
    ) -> Dict[str, str]:
        user_content = _build_user_payload(tgt_locale, chunk)
        last_err: Optional[Exception] = None
        use_json_format = True

        _safe_cb(
            progress_cb,
            {
                "event": "chunk_start",
                "chunk_index": chunk_idx_1,
                "chunk_total": chunk_total,
                "items": len(chunk),
                "src_lang": src_lang,
                "tgt_locale": tgt_locale,
            },
        )

        t0 = time.time()
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

                _safe_cb(
                    progress_cb,
                    {
                        "event": "chunk_done",
                        "chunk_index": chunk_idx_1,
                        "chunk_total": chunk_total,
                        "items": len(chunk),
                        "elapsed_s": round(time.time() - t0, 3),
                        "src_lang": src_lang,
                        "tgt_locale": tgt_locale,
                    },
                )
                return out

            except Exception as e:
                last_err = e
                msg = str(e).lower()

                _safe_cb(
                    progress_cb,
                    {
                        "event": "chunk_error",
                        "chunk_index": chunk_idx_1,
                        "chunk_total": chunk_total,
                        "attempt": attempt,
                        "retries": opt.retries,
                        "items": len(chunk),
                        "error": str(e)[:300],
                        "src_lang": src_lang,
                        "tgt_locale": tgt_locale,
                    },
                )

                # Some older endpoints/models may not support response_format.
                if use_json_format and "response_format" in msg:
                    use_json_format = False
                    continue

                if attempt < opt.retries:
                    _sleep_backoff(attempt, opt.backoff_base, opt.backoff_jitter)

        # If chunk fails permanently and it's a single item, surface error.
        if len(chunk) == 1:
            raise TranslationError(f"Chunk failed permanently: {last_err}")

        # If a larger chunk fails, split it and retry.
        items = list(chunk.items())
        mid = len(items) // 2
        left = dict(items[:mid])
        right = dict(items[mid:])
        _safe_cb(
            progress_cb,
            {
                "event": "chunk_split",
                "chunk_index": chunk_idx_1,
                "chunk_total": chunk_total,
                "left_items": len(left),
                "right_items": len(right),
                "src_lang": src_lang,
                "tgt_locale": tgt_locale,
            },
        )
        out_left = translate_chunk(left, chunk_idx_1, chunk_total)
        out_right = translate_chunk(right, chunk_idx_1, chunk_total)
        out_left.update(out_right)
        return out_left

    for idx0, ch in enumerate(chunks):
        out_part = translate_chunk(ch, idx0 + 1, len(chunks))
        merged.update(out_part)

    if set(merged.keys()) != set(src_dict.keys()):
        raise TranslationError("Final key mismatch")

    _safe_cb(
        progress_cb,
        {
            "event": "all_done",
            "src_lang": src_lang,
            "tgt_locale": tgt_locale,
            "total_items": len(src_dict),
            "chunks": len(chunks),
        },
    )

    return merged

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Iterable

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
    context_limit: int = 128_000

    # ✅ Input (system + user payload) must not exceed this fraction of context_limit.
    # You asked: "cannot exceed half of API token limit" => 0.70
    input_budget_ratio: float = 0.70

    # Additional safety headroom (messages overhead, token counting drift).
    overhead_tokens: int = 64

    # Chunk sizing
    max_chunk_items: int = 250

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
    return _multiset(_extract_placeholders(src)) == _multiset(_extract_placeholders(tgt))


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
# Chunking (pre-check full dict; no API call needed)
# =========================================================

def _input_budget(opt: _Options) -> int:
    return int(opt.context_limit * opt.input_budget_ratio)


def _chunk_flat_dict(
        src: Dict[str, str],
        estimator: _TokenEstimator,
        system_prompt: str,
        tgt_locale: str,
        opt: _Options,
) -> List[Dict[str, str]]:
    if not src:
        return []

    budget = _input_budget(opt)
    sys_tokens = estimator.count(system_prompt)

    def payload_tokens(d: Dict[str, str]) -> int:
        return estimator.count(_build_user_payload(tgt_locale, d))

    def total_tokens(d: Dict[str, str]) -> int:
        # Only counts input-side tokens (system+user), plus conservative overhead.
        return sys_tokens + payload_tokens(d) + opt.overhead_tokens

    # ✅ 1) Pre-check: whole dict fits => single chunk
    if total_tokens(src) <= budget:
        return [src]

    # ✅ 2) Otherwise: incremental chunking by key insertion
    chunks: List[Dict[str, str]] = []
    cur: Dict[str, str] = {}

    for k, v in src.items():
        single = {k: v}

        # Single item too large for input budget => fail early
        if total_tokens(single) > budget:
            raise TranslationError(
                "Single item exceeds input budget. "
                f"key={k!r} sys_tokens={sys_tokens} payload_tokens={payload_tokens(single)} "
                f"total={total_tokens(single)} budget={budget} context_limit={opt.context_limit}"
            )

        if not cur:
            cur = single
            continue

        cand = dict(cur)
        cand[k] = v

        if total_tokens(cand) > budget or len(cand) > opt.max_chunk_items:
            chunks.append(cur)
            cur = single
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
        raise ValueError(
            f"Key mismatch: missing={list(exp - got_set)[:5]} extra={list(got_set - exp)[:5]}"
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
        # Optional: allow overriding opt defaults from call sites
        opt: Optional[_Options] = None,
) -> Dict[str, str]:
    """Translate a flat dict: {key: text} -> {key: translated_text}."""
    if not OpenAI:
        raise SystemExit("OpenAI SDK 未安装，请先 pip install openai>=1.0.0")

    opt = opt or _Options()

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

    # ✅ Chunking is decided BEFORE any API request (token pre-check only)
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

        # If chunk fails permanently and it's a single item, surface error.
        if len(chunk) == 1:
            raise TranslationError(f"Chunk failed permanently: {last_err}")

        # If a larger chunk fails, split it and retry (rare with good pre-chunking,
        # but still useful for transient issues or unexpected tokenization drift).
        items = list(chunk.items())
        mid = len(items) // 2
        left = dict(items[:mid])
        right = dict(items[mid:])
        out_left = translate_chunk(left)
        out_right = translate_chunk(right)
        out_left.update(out_right)
        return out_left

    for ch in chunks:
        merged.update(translate_chunk(ch))

    if set(merged.keys()) != set(src_dict.keys()):
        raise TranslationError("Final key mismatch")

    return merged

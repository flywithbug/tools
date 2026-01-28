from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Pattern, Union

from .client import OpenAIClientFactory
from .models import OpenAIModel


# =========================================================
# Errors
# =========================================================

class TranslationError(RuntimeError):
    pass


# =========================================================
# Internal options
# =========================================================

@dataclass(frozen=True)
class _Options:
    timeout: float = 60.0
    retries: int = 2
    temperature: float = 0.0
    top_p: float = 1.0
    backoff_base: float = 1.6
    backoff_jitter: float = 0.25

    prefer_response_format_json: bool = True
    placeholder_guard: bool = True

    # If placeholders still mismatch, do ONE extra retry with stricter placeholder rule.
    placeholder_retry_once: bool = True

    # If still mismatching after the extra retry:
    # - If True: fallback to source for "safe" items only (URL / placeholder-only).
    # - If False: fallback to "" (recommended to avoid mixing languages).
    placeholder_fallback_safe_to_source: bool = False


# =========================================================
# Placeholder protection (auto: supports {{var}} and {var})
# =========================================================

# Notes:
# - We support mustache/handlebars: {{var}}
# - We also support single-brace variables: {name} / {count} (only "variable-ish" tokens)
# - Keep ${var}, %{var}, printf-style, and %% as they are common in i18n.
_PLACEHOLDER_RE: Pattern[str] = re.compile(
    r"(?:"
    r"{{\s*[A-Za-z0-9_.-]+\s*}}"            # {{name}}
    r"|{[A-Za-z0-9_.-]+}"                   # {name}   (restricted to avoid false positives)
    r"|\$\{\s*[A-Za-z0-9_.-]+\s*\}"         # ${var}
    r"|%\{\s*[A-Za-z0-9_.-]+\s*\}"          # %{var}
    r"|%\(\s*[A-Za-z0-9_.-]+\s*\)[a-zA-Z]"  # %(name)s
    r"|%(?:\d+\$)?[#0\- +']*\d*(?:\.\d+)?[a-zA-Z@]"  # %1$@, %d, %.2f, %@ ...
    r"|%%"                                   # %%
    r")"
)

_PLACEHOLDER_EXAMPLES = (
    "{{name}}, {name}, ${name}, %{name}, %(name)s, %1$s, %@, %.2f, %%"
)


# =========================================================
# Small helpers
# =========================================================

def _sleep_backoff(attempt: int, base: float, jitter: float) -> None:
    time.sleep((base ** attempt) + random.uniform(0, jitter))


def _extract_placeholders(text: str) -> List[str]:
    return [m.group(0) for m in _PLACEHOLDER_RE.finditer(text or "")]


def _multiset(xs: List[str]) -> Dict[str, int]:
    d: Dict[str, int] = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return d


def _placeholders_compatible(src: str, tgt: str) -> bool:
    return _multiset(_extract_placeholders(src)) == _multiset(_extract_placeholders(tgt))


def _guard_placeholders_list(src_items: List[str], out_items: List[str]) -> List[str]:
    """
    Best-effort: if placeholder multiset mismatches, replace placeholders in tgt
    with src placeholders in order. This fixes renamed/reordered placeholders in many cases,
    but cannot fix placeholders that are completely dropped (no match to replace).
    """
    fixed: List[str] = []
    for src_text, tgt_text in zip(src_items, out_items):
        src_ph = _extract_placeholders(src_text)

        if not src_ph or _placeholders_compatible(src_text, tgt_text):
            fixed.append(tgt_text)
            continue

        it = iter(src_ph)
        fixed.append(_PLACEHOLDER_RE.sub(lambda m: next(it, m.group(0)), tgt_text))
    return fixed


def _is_url_only(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    return bool(re.fullmatch(r"https?://\S+|www\.\S+", s))


def _is_placeholder_only(s: str) -> bool:
    """
    Returns True if, after removing placeholders and whitespace/punctuation,
    the string is effectively nothing. This marks items safe to fallback to source.
    """
    s0 = (s or "").strip()
    if not s0:
        return True
    removed = _PLACEHOLDER_RE.sub("", s0)
    removed = re.sub(r"[\s\(\)\[\]\{\},.:;!?\-_/\\|\"'`~@#$%^&*=+<>]+", "", removed)
    return removed == ""


def _finalize_placeholders_list(
        src_items: List[str],
        out_items: List[str],
        *,
        fallback_safe_to_source: bool,
) -> List[str]:
    """
    Ensure placeholders are compatible.
    If still mismatching:
      - fallback to "" (recommended) OR
      - fallback to source only for "safe" items (URL-only / placeholder-only).
    """
    finalized: List[str] = []
    for src_text, tgt_text in zip(src_items, out_items):
        if _placeholders_compatible(src_text, tgt_text):
            finalized.append(tgt_text)
            continue

        if fallback_safe_to_source and (_is_url_only(src_text) or _is_placeholder_only(src_text)):
            finalized.append(src_text)
        else:
            finalized.append("")
    return finalized


# =========================================================
# Prompt & payload (List[str] version)
# =========================================================
def _build_system_prompt_list(*, src_lang: str, tgt_locale: str, prompt_en: Optional[str]) -> str:
    base = (
        "You are a professional localization translator for mobile UI.\n"
        f"Translate from {src_lang} to {tgt_locale}.\n\n"

        "OUTPUT (STRICT):\n"
        '- Return ONLY valid JSON: {"translations":[...]}.\n'
        "- The array length and order MUST exactly match the input list (1:1).\n"
        "- No extra text, keys, or formatting.\n\n"

        "RULES:\n"
        f"- Write in {tgt_locale}.\n"
        "- Strings may contain parameters like {{name}}; keep them intact.\n"
        f"- Preserve ALL placeholders/format tokens exactly as-is (e.g., {_PLACEHOLDER_EXAMPLES}).\n"
        "- Preserve URLs and explicit brand/proper nouns verbatim.\n\n"

        "ABBREVIATIONS & TERMS:\n"
        "- If the English source contains abbreviations, use the appropriate abbreviations in the target language.\n"
        "- If an abbreviation or term has no clear, standard translation, keep it in English.\n\n"

        "QUALITY REQUIREMENT:\n"
        "- Always provide the best possible translation.\n"
        "- Do not omit content.\n"
        "- Do not return empty strings.\n"
    )

    extra = (prompt_en or "").strip()
    return base if not extra else f"{base}\nAdditional instructions:\n{extra}\n"


def _build_user_payload_list(items: List[str]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _parse_translation_list_object(text: str) -> List[str]:
    """
    Parse {"translations":[...]} and coerce to list[str] (None -> "").
    Strictly require each item to be str or None; otherwise raise to trigger retry.
    """
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Model output is not a JSON object")

    translations = obj.get("translations")
    if not isinstance(translations, list):
        raise ValueError("Invalid output: 'translations' must be an array")

    out: List[str] = []
    for v in translations:
        if v is None:
            out.append("")
        elif isinstance(v, str):
            out.append(v)
        else:
            raise ValueError("Invalid output: each translation must be a string or null")
    return out


# =========================================================
# OpenAI call helper
# =========================================================

def _chat_completion(
        client,
        *,
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


def _supports_response_format_error(msg_lower: str) -> bool:
    """
    Heuristic detection for models/endpoints that don't support response_format.
    """
    if "response_format" not in msg_lower:
        return False
    return any(x in msg_lower for x in ["unsupported", "unknown", "invalid", "not allowed", "unrecognized"])


def _has_placeholder_mismatch(src_items: List[str], out_items: List[str]) -> bool:
    for s, t in zip(src_items, out_items):
        if not _placeholders_compatible(s, t):
            return True
    return False


# =========================================================
# Public API
# =========================================================

def translate_list(
        *,
        prompt_en: Optional[str],
        src_items: List[str],
        src_lang: str,
        tgt_locale: str,
        model: Optional[Union[OpenAIModel, str]] = None,
        api_key: Optional[str] = None,
        opt: Optional[_Options] = None,
) -> List[str]:
    """
    Translate a flat list: [text, ...] -> [translated_text, ...] (same length, same order).

    - 不分片：调用方自行控制 src_items 长度
    - 无法翻译 => ""（空字符串）
    - 保留占位符/格式 token（自动支持 {{var}} 与 {var}，以及 printf/token 风格）
    """
    opt = opt or _Options()

    if not src_items:
        return []
    if any(not isinstance(x, str) for x in src_items):
        raise TranslationError("src_items must be list[str].")

    # model 统一取值：支持 Enum / str / None
    if model is None:
        model_name = OpenAIModel.GPT_4O.value
    elif isinstance(model, Enum):
        model_name = model.value  # type: ignore[assignment]
    else:
        model_name = str(model).strip() or OpenAIModel.GPT_4O.value

    base_system_prompt = _build_system_prompt_list(
        src_lang=src_lang,
        tgt_locale=tgt_locale,
        prompt_en=prompt_en,
    )

    client = OpenAIClientFactory(timeout=opt.timeout).create(api_key=api_key)
    user_content = _build_user_payload_list(src_items)

    last_err: Optional[Exception] = None
    use_json_format = True

    # We may do one extra "placeholder-focused" retry if placeholders mismatch.
    placeholder_extra_retry_used = False

    total_attempts = opt.retries + 1
    attempt = 0
    while attempt < total_attempts:
        try:
            out_text = _chat_completion(
                client,
                model=model_name,
                system_prompt=base_system_prompt,
                user_content=user_content,
                opt=opt,
                use_json_format=use_json_format,
            )

            translations = _parse_translation_list_object(out_text)

            if len(translations) != len(src_items):
                raise TranslationError(
                    f"Length mismatch: input {len(src_items)} items, output {len(translations)} translations."
                )

            if opt.placeholder_guard:
                translations = _guard_placeholders_list(src_items, translations)

                # If still mismatch, do ONE extra retry with stricter placeholder rule.
                if (
                        opt.placeholder_retry_once
                        and (not placeholder_extra_retry_used)
                        and _has_placeholder_mismatch(src_items, translations)
                ):
                    placeholder_extra_retry_used = True
                    stricter = (
                        "\nCRITICAL PLACEHOLDER RULE:\n"
                        "- Placeholders MUST be preserved EXACTLY.\n"
                        "- Do NOT add, remove, rename, reorder, or translate placeholders.\n"
                        '- If you cannot keep placeholders exactly, output "" for that item.\n'
                    )
                    base_system_prompt = base_system_prompt + stricter
                    attempt += 1
                    continue

                translations = _finalize_placeholders_list(
                    src_items,
                    translations,
                    fallback_safe_to_source=opt.placeholder_fallback_safe_to_source,
                )

            return translations

        except Exception as e:
            last_err = e
            msg = str(e).lower()

            if use_json_format and opt.prefer_response_format_json and _supports_response_format_error(msg):
                use_json_format = False
                attempt += 1
                continue

            if attempt < total_attempts - 1:
                _sleep_backoff(attempt, opt.backoff_base, opt.backoff_jitter)

        attempt += 1

    raise TranslationError(f"translate_list failed permanently: {last_err}")

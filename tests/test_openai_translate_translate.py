# tests/test_openai_translate_translate.py
from __future__ import annotations

import json
import types
import pytest

# 按你的目录结构：src/box_tools/_share/openai_translate/translate.py
from box_tools._share.openai_translate import translate as t
from box_tools._share.openai_translate.models import OpenAIModel


# ---------------------------
# Fake OpenAI client plumbing
# ---------------------------

class _FakeResp:
    def __init__(self, content: str):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]


class _FakeClient:
    """
    模拟 openai.OpenAI().chat.completions.create()
    通过 handler(messages, kwargs) -> content 来控制返回。
    """
    def __init__(self, handler):
        self._handler = handler

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                messages = kwargs.get("messages", [])
                content = self._outer._handler(messages, kwargs)
                return _FakeResp(content)

        class _Chat:
            def __init__(self, outer):
                self.completions = _Completions(outer)

        self.chat = _Chat(self)


@pytest.fixture()
def patch_client_factory(monkeypatch):
    """
    统一把 OpenAIClientFactory.create() 打补丁，返回 FakeClient
    使用：set_handler(fn) 来切换不同测试场景
    """
    state = {"handler": None}

    def create(_self, api_key=None):
        assert state["handler"] is not None, "handler must be set in test"
        return _FakeClient(state["handler"])

    monkeypatch.setattr(t.OpenAIClientFactory, "create", create)

    def set_handler(fn):
        state["handler"] = fn

    return set_handler


# ---------------------------
# Helpers
# ---------------------------

def _parse_user_payload(messages):
    # messages[1] should be user with JSON payload
    user = next(m for m in messages if m["role"] == "user")["content"]
    return json.loads(user)


# ---------------------------
# Tests
# ---------------------------

def test_translate_success_single_chunk_key_match_and_placeholder_guard(patch_client_factory):
    """
    - 输出必须是 JSON 对象
    - keys 必须匹配
    - placeholder_guard：如果模型把占位符搞错，要自动修正
    """
    src = {
        "greet": "Hello, {name}!",
        "price": "Total: %1$.2f USD",
    }

    def handler(messages, kwargs):
        payload = _parse_user_payload(messages)

        # 故意返回“占位符错乱”的结果，测试 _guard_placeholders 自动修复
        out = {
            "greet": "你好，{user}！",          # {user} 应该被修成 {name}
            "price": "合计：%s 美元",            # %s 应该被修成 %1$.2f
        }
        return json.dumps(out, ensure_ascii=False)

    patch_client_factory(handler)

    out = t.translate_flat_dict(
        prompt_en=None,
        src_dict=src,
        src_lang="en",
        tgt_locale="zh-Hans",
        model=OpenAIModel.GPT_4O_MINI,
        api_key="sk-test",
    )

    assert out["greet"] == "你好，{name}！"
    assert out["price"] == "合计：%1$.2f 美元"
    assert set(out.keys()) == set(src.keys())


def test_translate_reject_key_mismatch_strict(patch_client_factory):
    """
    strict_key_match=True 时：缺 key / 多 key 都应失败
    """
    src = {"a": "Hello", "b": "World"}

    def handler(messages, kwargs):
        return json.dumps({"a": "你好"}, ensure_ascii=False)  # 少了 b

    patch_client_factory(handler)

    with pytest.raises(Exception):
        t.translate_flat_dict(
            prompt_en=None,
            src_dict=src,
            src_lang="en",
            tgt_locale="zh-Hans",
            model="gpt-4o-mini",
            api_key="sk-test",
        )


def test_translate_non_strict_allows_extra_but_requires_missing_raises(patch_client_factory):
    """
    strict_key_match=False 时：
    - 允许 extra key（但 translate.py 当前实现仍会 raise：因为 _validate_keys 非 strict 只检查 missing）
    - 缺 key 仍应失败
    """
    src = {"a": "Hello", "b": "World"}

    def handler_missing(messages, kwargs):
        return json.dumps({"a": "你好", "c": "多余"}, ensure_ascii=False)  # 缺 b

    patch_client_factory(handler_missing)

    opt = t._Options(strict_key_match=False)

    with pytest.raises(Exception):
        t.translate_flat_dict(
            prompt_en=None,
            src_dict=src,
            src_lang="en",
            tgt_locale="zh-Hans",
            model="gpt-4o-mini",
            api_key="sk-test",
            opt=opt,
        )


def test_response_format_fallback_when_unsupported(patch_client_factory):
    """
    第一次调用带 response_format 时报错（模拟“response_format 不支持”）
    translate 应自动降级重试（use_json_format=False）并成功。
    """
    src = {"a": "Hello"}

    calls = {"n": 0}

    def handler(messages, kwargs):
        calls["n"] += 1
        # 第一次：带 response_format => 抛错
        if "response_format" in kwargs:
            raise RuntimeError("response_format is not supported on this model")
        # 第二次：不带 response_format => 成功
        return json.dumps({"a": "你好"}, ensure_ascii=False)

    patch_client_factory(handler)

    out = t.translate_flat_dict(
        prompt_en=None,
        src_dict=src,
        src_lang="en",
        tgt_locale="zh-Hans",
        model="gpt-4o-mini",
        api_key="sk-test",
        opt=t._Options(retries=1),
    )

    assert out == {"a": "你好"}
    assert calls["n"] == 2


def test_chunking_splits_and_merges_multiple_chunks(patch_client_factory):
    """
    强制较小的 context_limit，让 chunking 一定分块；
    验证会多次调用并最终合并结果。
    """
    src = {f"k{i}": "Hello " + ("x" * 200) for i in range(10)}

    def handler(messages, kwargs):
        payload = _parse_user_payload(messages)
        out = {k: f"ZH:{v[:5]}" for k, v in payload.items()}
        return json.dumps(out, ensure_ascii=False)

    patch_client_factory(handler)

    # 关键：budget 必须 > system_prompt token
    opt = t._Options(
        context_limit=2000,       # ✅ 让 budget = 2000*0.4 = 800，大于 sys_tokens(≈454)
        input_budget_ratio=0.4,
        overhead_tokens=16,
        max_chunk_items=3,        # ✅ 强制分块（每块最多3个）
        retries=0,
    )

    out = t.translate_flat_dict(
        prompt_en=None,
        src_dict=src,
        src_lang="en",
        tgt_locale="zh-Hans",
        model="gpt-4o-mini",
        api_key="sk-test",
        opt=opt,
    )

    assert set(out.keys()) == set(src.keys())
    assert out["k0"].startswith("ZH:")


def test_single_item_exceeds_budget_raises(patch_client_factory):
    """
    如果单个 key/value 就超过 input budget，应该在 chunking 阶段直接失败，
    不应该去调 API。
    """
    src = {"huge": "x" * 10000}

    called = {"n": 0}

    def handler(messages, kwargs):
        called["n"] += 1
        return json.dumps({"huge": "不该被调用"}, ensure_ascii=False)

    patch_client_factory(handler)

    opt = t._Options(
        context_limit=300,       # 极小
        input_budget_ratio=0.4,
        overhead_tokens=16,
        retries=0,
    )

    with pytest.raises(t.TranslationError):
        t.translate_flat_dict(
            prompt_en=None,
            src_dict=src,
            src_lang="en",
            tgt_locale="zh-Hans",
            model=OpenAIModel.GPT_4O_MINI,
            api_key="sk-test",
            opt=opt,
        )

    assert called["n"] == 0


def test_large_chunk_failure_splits_recursively(patch_client_factory):
    """
    如果一个较大的 chunk 失败（比如 transient 错误），translate_chunk 会二分拆分重试。
    这里模拟：当 chunk 大小 >= 2 时抛错；单个 item 才成功。
    """
    src = {"a": "Hello", "b": "World", "c": "Again"}

    def handler(messages, kwargs):
        payload = _parse_user_payload(messages)
        if len(payload) >= 2:
            raise RuntimeError("transient error")
        (k, v), = payload.items()
        return json.dumps({k: f"ZH:{v}"}, ensure_ascii=False)

    patch_client_factory(handler)

    out = t.translate_flat_dict(
        prompt_en=None,
        src_dict=src,
        src_lang="en",
        tgt_locale="zh-Hans",
        model="gpt-4o-mini",
        api_key="sk-test",
        opt=t._Options(retries=0),
    )

    assert out == {"a": "ZH:Hello", "b": "ZH:World", "c": "ZH:Again"}


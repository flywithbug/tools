from __future__ import annotations

import builtins
import json
import pytest

from box_tools.ai.translate import tool as tr_tool


class _Recorder:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture()
def capture_print(monkeypatch):
    """
    捕获 print 输出，避免用 capsys 时和 input 交互 patch 互相干扰。
    """
    out = []

    def _fake_print(*args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        out.append(sep.join(str(a) for a in args) + end)

    monkeypatch.setattr(builtins, "print", _fake_print)
    return out


def _patch_inputs(monkeypatch, inputs: list[str]):
    it = iter(inputs)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(it))


def test_translate_interactive_pick_lang_by_index_and_translate_then_exit(monkeypatch, capture_print):
    """
    场景：
    - 进入后用序号选择 source/target
    - 输入一段文本翻译
    - /exit 退出
    """
    # 1: source=en, 2: target=zh-Hans, then text, then exit
    _patch_inputs(monkeypatch, ["1", "2", "Hello world", "/exit"])

    rec = _Recorder()

    def fake_translate_flat_dict(**kwargs):
        rec.record(**kwargs)
        # translate tool 只用 key="text"
        return {"text": "你好，世界"}

    monkeypatch.setattr(tr_tool, "translate_flat_dict", fake_translate_flat_dict)

    code = tr_tool.main(["--model", "gpt-4o-mini", "--api-key", "sk-test"])
    assert code == 0

    # 断言调用 translate_flat_dict 的参数正确
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["src_dict"] == {"text": "Hello world"}
    assert call["src_lang"] == "en"
    assert call["tgt_locale"] == "zh-Hans"
    assert call["model"] == "gpt-4o-mini"
    assert call["api_key"] == "sk-test"

    # 断言打印包含译文
    joined = "".join(capture_print)
    assert "你好，世界" in joined


def test_translate_interactive_pick_lang_by_code(monkeypatch, capture_print):
    """
    场景：用语言 code 选择（source=ja target=fr）
    """
    _patch_inputs(monkeypatch, ["ja", "fr", "こんにちは", "/exit"])

    def fake_translate_flat_dict(**kwargs):
        return {"text": "Bonjour"}

    monkeypatch.setattr(tr_tool, "translate_flat_dict", fake_translate_flat_dict)

    code = tr_tool.main(["--api-key", "sk-test"])
    assert code == 0

    joined = "".join(capture_print)
    assert "Bonjour" in joined


def test_translate_skip_menu_with_args(monkeypatch, capture_print):
    """
    场景：--source/--target 指定后不走选项表，直接翻译
    """
    _patch_inputs(monkeypatch, ["Hello", "/exit"])

    rec = _Recorder()

    def fake_translate_flat_dict(**kwargs):
        rec.record(**kwargs)
        return {"text": "你好"}

    monkeypatch.setattr(tr_tool, "translate_flat_dict", fake_translate_flat_dict)

    code = tr_tool.main([
        "--source", "en",
        "--target", "zh-Hans",
        "--model", "gpt-4o-mini",
        "--api-key", "sk-test",
    ])
    assert code == 0

    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["src_lang"] == "en"
    assert call["tgt_locale"] == "zh-Hans"
    assert call["src_dict"] == {"text": "Hello"}

    joined = "".join(capture_print)
    assert "你好" in joined


def test_translate_swap_changes_direction(monkeypatch, capture_print):
    """
    场景：
    - 选 en -> zh-Hans
    - /swap 变成 zh-Hans -> en
    - 翻译一次，检查传入 translate_flat_dict 的 src_lang/tgt_locale
    """
    _patch_inputs(monkeypatch, ["en", "zh-Hans", "/swap", "你好", "/exit"])

    rec = _Recorder()

    def fake_translate_flat_dict(**kwargs):
        rec.record(**kwargs)
        return {"text": "Hello"}

    monkeypatch.setattr(tr_tool, "translate_flat_dict", fake_translate_flat_dict)

    code = tr_tool.main(["--api-key", "sk-test"])
    assert code == 0

    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["src_lang"] == "zh-Hans"
    assert call["tgt_locale"] == "en"
    assert call["src_dict"] == {"text": "你好"}

    joined = "".join(capture_print)
    assert "Hello" in joined


def test_translate_commands_show_and_langs(monkeypatch, capture_print):
    """
    场景：/show /langs /help 不应崩溃，并能继续翻译后退出
    """
    _patch_inputs(monkeypatch, ["en", "zh-Hans", "/show", "/langs", "/help", "Hi", "/exit"])

    def fake_translate_flat_dict(**kwargs):
        return {"text": "嗨"}

    monkeypatch.setattr(tr_tool, "translate_flat_dict", fake_translate_flat_dict)

    code = tr_tool.main(["--api-key", "sk-test"])
    assert code == 0

    joined = "".join(capture_print)
    # 不强绑定具体菜单文本，只检查关键输出存在
    assert "当前：" in joined
    assert "可选语言" in joined
    assert "指令：" in joined
    assert "嗨" in joined

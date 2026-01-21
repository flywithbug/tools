# tests/test_ai_chat_tool.py
import json
from pathlib import Path
import types
import pytest

# 你的模块路径：src/box_tools/ai/chat/tool.py
from box_tools.ai.chat import tool as chat_tool


class FakeSession:
    def __init__(self, messages=None):
        self.messages = messages or []


def test_format_ts_for_display_ok():
    ts = "2026-01-21T13:05:12+08:00"
    assert chat_tool._format_ts_for_display(ts) == "13:05:12"


def test_format_ts_for_display_invalid():
    assert chat_tool._format_ts_for_display("not-a-ts") == "--:--:--"
    assert chat_tool._format_ts_for_display(None) == "--:--:--"


def test_ensure_last_two_have_ts_adds_missing_ts(monkeypatch):
    # 固定时间戳，避免 flaky
    monkeypatch.setattr(chat_tool, "_local_iso_ts", lambda: "2026-01-21T10:00:00+08:00")

    s = FakeSession(
        messages=[
            {"role": "user", "content": "hi"},               # no ts
            {"role": "assistant", "content": "hello"},       # no ts
        ]
    )
    chat_tool._ensure_last_two_have_ts(s)  # type: ignore

    assert s.messages[-1]["ts"] == "2026-01-21T10:00:00+08:00"
    assert s.messages[-2]["ts"] == "2026-01-21T10:00:00+08:00"


def test_get_last_assistant_text_picks_latest():
    s = FakeSession(
        messages=[
            {"role": "assistant", "content": "old"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "new"},
        ]
    )
    assert chat_tool._get_last_assistant_text(s) == "new"


def test_get_last_assistant_text_none_when_missing():
    s = FakeSession(messages=[{"role": "user", "content": "q"}])
    assert chat_tool._get_last_assistant_text(s) is None


def test_dump_and_load_session_roundtrip(tmp_path: Path):
    # 用真 JSON 往返测试
    meta = {"session_id": "sid", "model": "gpt-x"}
    # 用真实 ChatSession 类型更接近生产；但如果导入有问题，也可用 FakeSession
    sess = chat_tool.ChatSession(system_prompt="SYS")  # type: ignore
    sess.messages = [
        {"role": "user", "content": "hi", "ts": "2026-01-21T10:00:00+08:00"},
        {"role": "assistant", "content": "ok", "ts": "2026-01-21T10:00:01+08:00"},
    ]

    p = tmp_path / "s.json"
    chat_tool._dump_session(p, sess, meta)
    assert p.exists()

    sess2, meta2 = chat_tool._load_session(p)
    assert meta2["session_id"] == "sid"
    assert meta2["model"] == "gpt-x"
    assert sess2.system_prompt == "SYS"
    assert sess2.messages == sess.messages


def test_copy_to_clipboard_macos_pbcopy(monkeypatch):
    # 模拟 macOS 环境
    monkeypatch.setattr(chat_tool.sys, "platform", "darwin")

    writes = {"data": b"", "closed": False}

    class FakeStdin:
        def write(self, b):
            writes["data"] += b

        def close(self):
            writes["closed"] = True

    class FakePopen:
        def __init__(self, cmd, stdin):
            assert cmd == ["pbcopy"]
            self.stdin = FakeStdin()

        def wait(self):
            return 0

    monkeypatch.setattr(chat_tool.subprocess, "Popen", FakePopen)

    ok, msg = chat_tool._copy_to_clipboard("你好")
    assert ok is True
    assert "已复制" in msg
    assert writes["data"] == "你好".encode("utf-8")
    assert writes["closed"] is True


def test_copy_to_clipboard_windows_clip(monkeypatch):
    monkeypatch.setattr(chat_tool.sys, "platform", "win32")

    writes = {"data": b""}

    class FakeStdin:
        def write(self, b):
            writes["data"] += b

        def close(self):
            pass

    class FakePopen:
        def __init__(self, cmd, stdin):
            assert cmd == ["clip"]
            self.stdin = FakeStdin()

        def wait(self):
            return 0

    monkeypatch.setattr(chat_tool.subprocess, "Popen", FakePopen)

    ok, msg = chat_tool._copy_to_clipboard("abc")
    assert ok is True
    assert "已复制" in msg
    # Windows 分支用 utf-16le
    assert writes["data"] == "abc".encode("utf-16le")


def test_copy_to_clipboard_linux_fallback_wl_copy(monkeypatch):
    monkeypatch.setattr(chat_tool.sys, "platform", "linux")

    called = {"wl": 0, "xclip": 0}
    writes = {"data": b""}

    class FakeStdin:
        def write(self, b):
            writes["data"] += b

        def close(self):
            pass

    def fake_popen(cmd, stdin):
        if cmd == ["wl-copy"]:
            called["wl"] += 1
            p = types.SimpleNamespace(stdin=FakeStdin(), wait=lambda: 0)
            return p
        if cmd == ["xclip", "-selection", "clipboard"]:
            called["xclip"] += 1
            p = types.SimpleNamespace(stdin=FakeStdin(), wait=lambda: 0)
            return p
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr(chat_tool.subprocess, "Popen", fake_popen)

    ok, msg = chat_tool._copy_to_clipboard("linux")
    assert ok is True
    assert called["wl"] == 1
    assert called["xclip"] == 0
    assert writes["data"] == b"linux"
    assert "已复制" in msg

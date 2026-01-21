# src/box_tools/ai/chat/tool.py
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from box_tools._share.openai_translate.models import OpenAIModel
from box_tools._share.openai_translate.chat import OpenAIChat, ChatOptions, ChatSession


BOX_TOOL = {
    "id": "ai.chat",
    "name": "box_ai_chat",
    "category": "ai",
    "summary": "命令行连续对话：输入问题→等待 AI 回复→继续追问（支持 /new /reset /save /load /model 等）",
    "usage": [
        "box_ai_chat",
        "box_ai_chat --model gpt-4o-mini",
        "box_ai_chat --system \"You are a helpful assistant.\"",
        "box_ai_chat --load ~/.box_tools/ai_chat/20260121_120000.json",
    ],
    "options": [
        {"flag": "--model", "desc": "指定模型（默认 gpt-4o-mini，如 gpt-4o / gpt-4.1 / gpt-4.1-mini）"},
        {"flag": "--system", "desc": "设置 system prompt（对话角色/风格）"},
        {"flag": "--temperature", "desc": "采样温度（默认 0.2；越低越稳定）"},
        {"flag": "--top-p", "desc": "top_p（默认 1.0）"},
        {"flag": "--timeout", "desc": "请求超时（秒，默认 30）"},
        {"flag": "--api-key", "desc": "显式传入 OpenAI API Key（不传则读取 OPENAI_API_KEY）"},
        {"flag": "--load", "desc": "启动时加载会话文件（JSON）"},
        {"flag": "--session", "desc": "指定 session id（用于固定默认保存文件名）"},
        {"flag": "--store-dir", "desc": "会话保存目录（默认 ~/.box_tools/ai_chat）"},
    ],
    "examples": [
        {"cmd": "export OPENAI_API_KEY='sk-***' && box_ai_chat", "desc": "进入连续对话模式"},
        {"cmd": "box_ai_chat --model gpt-4o-mini", "desc": "用指定模型聊天"},
        {"cmd": "box_ai_chat --system \"You are a senior iOS engineer.\"", "desc": "用自定义 system prompt 进入对话"},
        {"cmd": "box_ai_chat --load ~/.box_tools/ai_chat/20260121_120000.json", "desc": "加载历史会话继续聊"},
    ],
    "docs": "README.md",
}


DEFAULT_STORE_DIR = Path.home() / ".box_tools" / "ai_chat"


def _now_session_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _ensure_store_dir(store_dir: Path) -> Path:
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir


def _print_help() -> None:
    print(
        "指令：\n"
        "  /help                帮助\n"
        "  /exit                退出\n"
        "  /new                 新会话（生成新 session id）\n"
        "  /reset               清空当前会话\n"
        "  /model <name>        切换模型（如 gpt-4o-mini）\n"
        "  /system <text>       设置 system prompt\n"
        "  /save [path]         保存会话（默认 ~/.box_tools/ai_chat/<session>.json）\n"
        "  /load <path>         加载会话\n"
        "  /history [n]         打印最近 n 条（默认 20）\n"
    )


def _dump_session(path: Path, session: ChatSession, meta: Dict[str, str]) -> None:
    data = {
        "meta": meta,
        "system_prompt": session.system_prompt,
        "messages": session.messages,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_session(path: Path) -> tuple[ChatSession, Dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sess = ChatSession(system_prompt=raw.get("system_prompt") or "You are a helpful assistant.")
    sess.messages = list(raw.get("messages") or [])
    meta = dict(raw.get("meta") or {})
    return sess, meta


def _print_history(session: ChatSession, n: int = 20) -> None:
    msgs = session.messages[-n:]
    if not msgs:
        print("(空)")
        return
    for m in msgs:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        print(f"[{role}] {content}")


def _normalize_model(m: str) -> str:
    s = (m or "").strip()
    return s if s else OpenAIModel.GPT_4O_MINI.value


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog=BOX_TOOL["name"], add_help=True)
    parser.add_argument("--model", default=OpenAIModel.GPT_5_CHAT.value, help="模型名，如 gpt-5-chat-latest")
    parser.add_argument("--system", default="You are a helpful assistant.", help="system prompt")
    parser.add_argument("--api-key", default=None, help="可选：显式传入 OpenAI API key（不传则读 OPENAI_API_KEY）")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--load", default=None, help="启动时加载会话文件路径（JSON）")
    parser.add_argument("--session", default=None, help="指定 session id（用于固定默认保存文件名）")
    parser.add_argument("--store-dir", default=str(DEFAULT_STORE_DIR), help="会话保存目录（默认 ~/.box_tools/ai_chat）")

    args = parser.parse_args(argv)

    store_dir = _ensure_store_dir(Path(args.store_dir).expanduser())
    session_id = (args.session or "").strip() or _now_session_id()

    # 初始化 chat client（内部会自动检测 OPENAI_API_KEY，不存在会提示配置方法）
    opt = ChatOptions(
        model=_normalize_model(args.model),
        temperature=args.temperature,
        top_p=args.top_p,
        timeout=args.timeout,
    )
    chat = OpenAIChat(api_key=args.api_key, opt=opt)

    # 初始化 session / meta
    meta: Dict[str, str] = {"session_id": session_id, "model": _normalize_model(args.model)}
    session = ChatSession(system_prompt=args.system)

    # 启动加载
    if args.load:
        try:
            session, meta2 = _load_session(Path(args.load).expanduser())
            meta.update(meta2)

            # session_id 优先：load 文件里的 meta.session_id > CLI --session > now
            loaded_sid = (meta.get("session_id") or "").strip()
            session_id = loaded_sid or session_id
            meta["session_id"] = session_id

            # model 优先：load 文件里的 meta.model
            if meta.get("model"):
                chat.opt = ChatOptions(
                    model=_normalize_model(meta["model"]),
                    temperature=args.temperature,
                    top_p=args.top_p,
                    timeout=args.timeout,
                )

            print(f"已加载会话：{args.load}")
        except Exception as e:
            print(f"[错误] 加载会话失败：{e}")
            return 2

    print("进入对话模式：/help 查看指令。")
    print(f"session={meta['session_id']} model={chat.opt.model}")

    while True:
        try:
            user_text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0

        if not user_text:
            continue

        # ---- commands ----
        if user_text.startswith("/"):
            cmd = user_text.strip()

            if cmd == "/help":
                _print_help()
                continue

            if cmd in ("/exit", "/quit"):
                print("退出。")
                return 0

            if cmd == "/reset":
                session.reset()
                print("已清空当前会话。")
                continue

            if cmd == "/new":
                session_id = _now_session_id()
                meta["session_id"] = session_id
                session = ChatSession(system_prompt=session.system_prompt)
                print(f"已开启新会话：session={session_id}")
                continue

            if cmd.startswith("/model "):
                m = _normalize_model(cmd[len("/model "):].strip())
                chat.opt = ChatOptions(
                    model=m,
                    temperature=chat.opt.temperature,
                    top_p=chat.opt.top_p,
                    timeout=chat.opt.timeout,
                )
                meta["model"] = m
                print(f"已切换模型：{m}")
                continue

            if cmd.startswith("/system "):
                session.system_prompt = cmd[len("/system "):].strip()
                print("已更新 system prompt。")
                continue

            if cmd.startswith("/save"):
                parts = cmd.split(maxsplit=1)
                if len(parts) == 2:
                    path = Path(parts[1]).expanduser()
                else:
                    path = store_dir / f"{meta['session_id']}.json"
                try:
                    _dump_session(path, session, meta)
                    print(f"已保存：{path}")
                except Exception as e:
                    print(f"[错误] 保存失败：{e}")
                continue

            if cmd.startswith("/load "):
                path = Path(cmd[len("/load "):].strip()).expanduser()
                try:
                    session, meta2 = _load_session(path)
                    meta.update(meta2)

                    loaded_sid = (meta.get("session_id") or "").strip()
                    if loaded_sid:
                        session_id = loaded_sid
                        meta["session_id"] = session_id

                    if meta.get("model"):
                        chat.opt = ChatOptions(
                            model=_normalize_model(meta["model"]),
                            temperature=chat.opt.temperature,
                            top_p=chat.opt.top_p,
                            timeout=chat.opt.timeout,
                        )

                    print(f"已加载：{path}")
                except Exception as e:
                    print(f"[错误] 加载失败：{e}")
                continue

            if cmd.startswith("/history"):
                parts = cmd.split(maxsplit=1)
                n = 20
                if len(parts) == 2:
                    try:
                        n = int(parts[1])
                    except Exception:
                        n = 20
                _print_history(session, n=n)
                continue

            print("未知指令：/help 查看可用指令。")
            continue

        # ---- normal chat ----
        msgs = session.build_messages(user_text)
        try:
            ans = chat.complete(msgs)
        except Exception as e:
            print(f"[错误] {e}")
            continue

        print(ans)
        session.append_turn(user_text, ans)

    # unreachable
    # return 0


if __name__ == "__main__":
    raise SystemExit(main())

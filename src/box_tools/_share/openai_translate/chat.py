from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union
from enum import Enum

from .client import OpenAIClientFactory
from .models import OpenAIModel


@dataclass
class ChatSession:
    system_prompt: str = "You are a helpful assistant."
    messages: List[Dict[str, str]] = field(default_factory=list)

    def reset(self) -> None:
        self.messages.clear()

    def build_messages(self, user_text: str) -> List[Dict[str, str]]:
        msgs = [{"role": "system", "content": self.system_prompt}]
        msgs.extend(self.messages)
        msgs.append({"role": "user", "content": user_text})
        return msgs

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": assistant_text})


@dataclass(frozen=True)
class ChatOptions:
    model: Union[OpenAIModel, str] = OpenAIModel.GPT_4O_MINI
    temperature: float = 0.2
    top_p: float = 1.0
    timeout: float = 30.0


class OpenAIChat:
    def __init__(self, *, api_key: Optional[str] = None, opt: Optional[ChatOptions] = None):
        self.opt = opt or ChatOptions()
        self.client = OpenAIClientFactory(timeout=self.opt.timeout).create(api_key=api_key)

    def complete(self, messages: List[Dict[str, str]]) -> str:
        model_name = self.opt.model.value if isinstance(self.opt.model, Enum) else str(self.opt.model)
        resp = self.client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=self.opt.temperature,
            top_p=self.opt.top_p,
        )
        return (resp.choices[0].message.content or "").strip()


def run_chat_cli(
        *,
        api_key: Optional[str] = None,
        model: Union[OpenAIModel, str] = OpenAIModel.GPT_5_CHAT,
        system_prompt: str = "You are a helpful assistant.",
) -> int:
    chat = OpenAIChat(api_key=api_key, opt=ChatOptions(model=model))
    session = ChatSession(system_prompt=system_prompt)

    print("进入对话模式：/help 查看指令，/exit 退出，/reset 重置。")
    while True:
        try:
            user_text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0

        if not user_text:
            continue

        if user_text.startswith("/"):
            cmd = user_text.strip()
            if cmd in ("/exit", "/quit"):
                print("退出。")
                return 0
            if cmd == "/reset":
                session.reset()
                print("已重置会话。")
                continue
            if cmd.startswith("/model "):
                new_model = cmd[len("/model "):].strip()
                chat.opt = ChatOptions(model=new_model, timeout=chat.opt.timeout)
                print(f"已切换模型：{new_model}")
                continue
            if cmd.startswith("/system "):
                session.system_prompt = cmd[len("/system "):].strip()
                print("已更新 system prompt。")
                continue
            if cmd == "/help":
                print(
                    "指令：\n"
                    "  /help          帮助\n"
                    "  /exit          退出\n"
                    "  /reset         清空会话\n"
                    "  /model <name>  切换模型（如 gpt-4o-mini）\n"
                    "  /system <txt>  设置系统提示词\n"
                )
                continue

            print("未知指令：/help 查看可用指令。")
            continue

        msgs = session.build_messages(user_text)
        try:
            ans = chat.complete(msgs)
        except Exception as e:
            print(f"[错误] {e}")
            continue

        print(ans)
        session.append_turn(user_text, ans)

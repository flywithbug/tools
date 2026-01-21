from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


class OpenAIConfigError(RuntimeError):
    pass


def resolve_api_key(api_key: Optional[str] = None) -> str:
    key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise OpenAIConfigError(
            "未检测到 OpenAI API Key。\n"
            "请先在终端配置环境变量：\n"
            '  export OPENAI_API_KEY="sk-***"\n'
            "或在调用时显式传入 api_key。"
        )
    return key


@dataclass(frozen=True)
class OpenAIClientFactory:
    timeout: float = 30.0

    def create(self, api_key: Optional[str] = None) -> "OpenAI":
        if not OpenAI:
            raise SystemExit("OpenAI SDK 未安装，请先 pip install openai>=1.0.0")
        key = resolve_api_key(api_key)
        return OpenAI(api_key=key, timeout=self.timeout)

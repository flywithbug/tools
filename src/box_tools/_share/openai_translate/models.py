from enum import Enum

from enum import Enum

class OpenAIModel(str, Enum):
    # 4.x / 4o
    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"

    # 5.x
    GPT_5 = "gpt-5"
    GPT_5_CHAT = "gpt-5-chat-latest"          # ChatGPT 当前使用的 GPT-5 指针（偏“聊天最新”）
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_NANO = "gpt-5-nano"

    # 5.2（当前主推）
    GPT_5_2 = "gpt-5.2"
    GPT_5_2_PRO = "gpt-5.2-pro"
    GPT_5_2_CHAT = "gpt-5.2-chat-latest"      # ChatGPT 当前使用的 GPT-5.2 指针（偏“聊天最新”）
    GPT_5_2_CODEX = "gpt-5.2-codex"            # 指南中提到的 coding/agentic 变体

# box_ai_chat

一个功能完整、体验友好的命令行 AI 连续对话工具，支持会话管理、历史记录、时间戳、加载/保存、模型切换、复制回复等能力。

> 设计目标：
>
> * CLI 原生体验，但尽量接近“聊天应用”的可读性
> * 不强依赖 UI 库，`rich` 为可选增强
> * 会话可持久化、可恢复、可复用

---

## 功能特性

* 连续对话（上下文自动累积）
* 可切换模型（`/model`）
* 可修改 system prompt（`/system`）
* 会话保存 / 加载（JSON）
* 聊天记录带 **时间戳**
* AI 回复显示 **loading 动画 + 耗时**
* 历史记录美观展示（支持 Markdown）
* 一键复制 **上一条 GPT 回复完整内容** 到剪贴板
* 兼容旧会话文件（无时间戳也可正常显示）

---

## 安装依赖

最低依赖：

```bash
pip install openai PyYAML
```

推荐（开启美观 UI / loading 动画）：

```bash
pip install rich
```

Linux 复制功能建议额外安装（任选其一）：

```bash
# Wayland
sudo apt install wl-clipboard

# X11
sudo apt install xclip
```

---

## 环境变量

```bash
export OPENAI_API_KEY="sk-xxx"
```

或通过命令行参数传入：

```bash
box_ai_chat --api-key sk-xxx
```

---

## 使用方式

### 启动对话

```bash
box_ai_chat
```

指定模型：

```bash
box_ai_chat --model gpt-4o-mini
```

自定义 system prompt：

```bash
box_ai_chat --system "You are a senior iOS engineer."
```

加载历史会话：

```bash
box_ai_chat --load ~/.box_tools/ai_chat/20260121_120000.json
```

---

## 交互指令

在对话过程中输入以下命令（以 `/` 开头）：

| 指令               | 说明                                             |
| ---------------- | ---------------------------------------------- |
| `/help`          | 显示帮助                                           |
| `/exit` `/quit`  | 退出程序                                           |
| `/new`           | 开启新会话（新 session id）                            |
| `/reset`         | 清空当前会话内容                                       |
| `/model <name>`  | 切换模型                                           |
| `/system <text>` | 更新 system prompt                               |
| `/save [path]`   | 保存会话（默认 `~/.box_tools/ai_chat/<session>.json`） |
| `/load <path>`   | 加载会话文件                                         |
| `/history [n]`   | 查看最近 n 条记录（默认 20）                              |
| `/copy`          | **复制上一条 AI 回复完整内容到剪贴板**                        |

---

## 时间戳说明

* 每一条 `user` / `assistant` 消息都会记录时间戳
* 存储格式：ISO 8601（本地时间，带时区）

```json
{
  "role": "assistant",
  "content": "...",
  "ts": "2026-01-21T13:05:12+08:00"
}
```

* `/history` 显示为 `HH:MM:SS`
* 旧会话（无 `ts`）会显示为 `--:--:--`

---

## 复制能力说明（/copy）

`/copy` 会复制：

* 最近一条 `assistant / ai` 消息
* **完整原文**（不截断、不去 Markdown、不加 UI 装饰）

平台支持顺序：

1. macOS：`pbcopy`
2. Windows：`clip`
3. Linux：`wl-copy` → `xclip`
4. Python fallback：`pyperclip`（如已安装）

若复制失败，会给出明确提示。

---

## 会话文件结构

```json
{
  "meta": {
    "session_id": "20260121_120000",
    "model": "gpt-4o-mini"
  },
  "system_prompt": "You are a helpful assistant.",
  "messages": [
    {
      "role": "user",
      "content": "你好",
      "ts": "2026-01-21T13:00:01+08:00"
    },
    {
      "role": "assistant",
      "content": "你好！",
      "ts": "2026-01-21T13:00:02+08:00"
    }
  ]
}
```

---

## 设计取舍说明

* **rich 是可选依赖**：

    * 未安装时自动降级为纯 `print()`
    * 功能不受影响

* **不修改 ChatSession 内部结构**：

    * 时间戳通过外层工具补充
    * 降低与底层 SDK 的耦合

* **CLI 优先**：

    * 所有能力可通过键盘完成
    * 不依赖鼠标或图形界面

---

## 常见问题

### Q: 复制失败怎么办？

* macOS：确认 `pbcopy` 存在
* Linux：安装 `wl-clipboard` 或 `xclip`
* 或：`pip install pyperclip`

### Q: 会话能跨机器使用吗？

可以。会话文件是纯 JSON，与平台无关。

---

## License

Internal Tool · Davion Labs

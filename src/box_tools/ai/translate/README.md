# box_ai_translate

交互式多语言 AI 翻译工具。

通过命令行选择 **源语言（source）** 和 **目标语言（target）**，然后持续输入文本，AI 会实时翻译为目标语言。支持中途切换语言、交换方向并随时退出。

---

## 支持语言

* 英语（en）
* 中文简体（zh-Hans）
* 繁体中文（zh-Hant）
* 粤语（yue / 廣東話）
* 日语（ja）
* 韩语（ko）
* 法语（fr）

---

## 安装与准备

确保已配置 OpenAI API Key：

```bash
export OPENAI_API_KEY="sk-***"
```

工具安装完成后即可使用。

---

## 基本用法

### 进入交互式翻译（推荐）

```bash
box_ai_translate
```

启动后：

1. 通过选项表选择源语言（source）
2. 选择目标语言（target）
3. 直接输入文本即可翻译

---

### 跳过选项表，直接指定语言

```bash
box_ai_translate --source en --target zh-Hans
```

---

## 交互指令

在翻译模式下，可随时输入以下指令：

* `/help`  显示帮助
* `/exit`  退出工具
* `/langs` 显示支持的语言列表
* `/source` 重新选择源语言
* `/target` 重新选择目标语言
* `/swap`  交换 source 与 target
* `/show` 显示当前 source / target 状态

示例：

```text
> Hello world
你好，世界

> /swap
已交换 source/target

> 你好
Hello
```

---

## 常见用法示例

### 英语 → 简体中文

```bash
box_ai_translate --source en --target zh-Hans
```

### 日语 → 繁体中文

```bash
box_ai_translate --source ja --target zh-Hant
```

### 中文 → 法语

```bash
box_ai_translate --source zh-Hans --target fr
```

---

## 说明

* 本工具基于 AI 翻译，适合 UI 文案、技术文本、日常语言等场景
* 会自动保持变量占位符与格式（如 `{name}`、`%s` 等）
* 支持连续翻译，无需重复启动命令

---

如需连续对话能力，请使用：`box_ai_chat`。

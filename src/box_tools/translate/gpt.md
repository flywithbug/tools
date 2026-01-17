# gpt

`gpt` 是一个面向命令行的 OpenAI 小工具：

- 提供 **平铺 JSON（flat dict）翻译能力**：`{key: text}` → `{key: translated_text}`
- 保证 **key 完全不变**，只翻译 value
- 内置 **占位符/格式化 token 守护**（例如 `{name}`、`%1$s`、`%.2f`、`%%`）
- 自带 **环境自检（doctor）**，会检查本机是否配置了 `OPENAI_API_KEY`

> 设计目标：让“自动化本地化/翻译”更像一个靠谱的工程工具，而不是一次性问答。

---

## 安装

`gpt` 随工具集一起安装。

安装完成后可执行：

```sh
gpt --help
```

---

## 快速开始

直接运行（会显示简介 + 检查 `OPENAI_API_KEY` 是否配置）：

```sh
gpt
```

推荐首次使用先跑 doctor：

```sh
gpt doctor
```

---

## 配置 OPENAI_API_KEY

`gpt` 会优先读取环境变量：

- `OPENAI_API_KEY`

### macOS / Linux（bash / zsh）

临时生效（仅当前终端会话）：

```sh
export OPENAI_API_KEY="sk-..."
```

永久生效（推荐，写入 shell 配置）：

```sh
echo 'export OPENAI_API_KEY="sk-..."' >> ~/.zshrc
source ~/.zshrc
```

如果你用的是 bash，把 `~/.zshrc` 换成 `~/.bashrc` 或 `~/.bash_profile`。

### Windows PowerShell

当前会话：

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

永久：

```powershell
setx OPENAI_API_KEY "sk-..."
```

---

## 用法

### 1) 环境自检

```sh
gpt doctor
```

会检查：

- Python 版本
- `openai` SDK 是否可用
- `OPENAI_API_KEY` 是否配置

---

### 2) 翻译一个 JSON 文件（flat object）

输入文件必须是 **单层 JSON 对象**，例如：

```json
{
  "hello": "Hello",
  "bye": "Goodbye {name}"
}
```

翻译命令：

```sh
gpt translate --src-lang en --tgt-locale zh_Hant --in i18n/en.json --out i18n/zh_Hant.json
```

说明：

- 输出仍然是同样的 keys
- 自动守护 `{name}` 之类的占位符

---

## 常见问题

### 1) 提示 OpenAI SDK 未安装

```text
OpenAI SDK 未安装，请先 pip install openai>=1.0.0
```

在你的 Python 环境里安装：

```sh
pip install "openai>=1.0.0"
```

### 2) 提示 OPENAI_API_KEY 未配置

运行 `gpt` 或 `gpt doctor` 会直接提示如何设置环境变量。

---

## License

MIT

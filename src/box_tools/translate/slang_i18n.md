# slang_i18n

Flutter slang i18n 工具：针对 **flat** 的 `*.i18n.json` 做排序、冗余检查/清理，以及基于英文源文件的增量（或全量）翻译。

> 翻译能力由同目录 `gpt.py` 提供（placeholder 保护、严格 key 校验、分块/重试等）。

---

## 安装与依赖

- 运行环境：Python 3.9+
- 依赖：
  - `PyYAML>=6.0`
  - `openai>=1.0.0`

如果你是用 `pipx` 安装 `box`：

```bash
pipx inject box pyyaml "openai>=1.0.0"
```

---

## API Key 配置

翻译需要 OpenAI API Key，可二选一：

1) 环境变量（推荐）

```bash
# macOS/Linux
export OPENAI_API_KEY="sk-..."

# Windows PowerShell
setx OPENAI_API_KEY "sk-..."
```

2) 命令行参数

```bash
slang_i18n translate --api-key sk-...
```

---

## 快速开始

```bash
# 1) 生成配置
slang_i18n init

# 2) 检查环境/配置
slang_i18n doctor

# 3) 增量翻译（只补缺失 key）
slang_i18n translate

# 4) 排序（@@locale 保持最前）
slang_i18n sort

# 5) 检查冗余（多出来的 key）
slang_i18n check

# 6) 清理冗余（删除多余 key）
slang_i18n clean --yes
```

不带参数运行会进入交互菜单：

```bash
slang_i18n
```

---

## 目录与文件命名规则

工具默认在 **项目根目录**运行，并查找 `i18n/`。

### 分组规则

- 如果 `i18n/` 下 **存在任何子目录**：
  - 只处理这些子目录（当作模块），**不处理 i18n 根目录**
- 如果 `i18n/` 下 **没有子目录**：
  - 处理 `i18n/` 根目录

### 文件名规则

- 根目录：`i18n/{locale}.i18n.json`
- 模块目录：`i18n/<module>/{camelModule}_{locale}.i18n.json`

例：

- `i18n/en.i18n.json`
- `i18n/user_profile/userProfile_ja.i18n.json`

如果开启 `options.normalize_filenames: true`，会对模块目录下能“明确识别 locale”的文件做保守重命名（不覆盖目标文件）。

---

## 配置文件 slang_i18n.yaml

```yaml
source_locale: en
target_locales:
  - zh_Hant
  - ja
prompt_en: |
  Translate UI strings naturally.
options:
  sort_keys: true
  cleanup_extra_keys: true
  incremental_translate: true
  normalize_filenames: true
```

字段说明：

- `source_locale`：源语言（通常 en）
- `target_locales`：目标语言列表
- `prompt_en`：额外英文提示词（可空）
- `options.sort_keys`：保存 JSON 时按 key 排序（@@locale 会保持在最前）
- `options.cleanup_extra_keys`：翻译时会忽略目标文件里“源文件不存在”的 key（避免继续扩散）
- `options.incremental_translate`：默认只翻译缺失 key（命令行 `--full` 可切全量）
- `options.normalize_filenames`：模块目录文件名规范化（保守）

---

## 命令与参数

### slang_i18n init
生成 `slang_i18n.yaml` 模板（如果已存在则校验、不覆盖）。

### slang_i18n doctor
检查：OpenAI SDK / PyYAML / i18n 目录结构 / 配置文件 / API Key。

### slang_i18n sort
对所有 `*.i18n.json` 排序保存。

### slang_i18n check
报告冗余 key（目标语言中存在但源语言不存在的 key）。

- 默认：发现冗余时退出码为 **3**（便于 CI 卡住）
- 使用 `--no-exitcode-3`：即使发现冗余也返回 0

### slang_i18n clean
删除冗余 key。

- 默认会二次确认
- 使用 `--yes` 跳过确认

### slang_i18n translate
执行翻译。

- 默认增量（只补缺失 key）
- `--full`：全量翻译（用源文件覆盖全部 key）
- `--model`：指定模型（默认 `gpt-4o`）
- `--api-key`：传入 API key（也可用 `OPENAI_API_KEY`）

---

## Exit Codes

- `0`：成功
- `1`：执行失败
- `2`：环境/配置错误（doctor/init/config/i18n 结构问题等）
- `3`：check 发现冗余 key（默认启用，便于 CI）

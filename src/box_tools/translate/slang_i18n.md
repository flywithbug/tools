```markdown
# slang_i18n

`slang_i18n` 是一个 Flutter **slang i18n** 辅助工具，专注处理 **flat JSON（不嵌套）** 的多语言文件，并提供：

- ✅ 排序所有 `*.i18n.json`（`@@locale` 永远放第一）
- ✅ 增量翻译（`en` 新增 key 自动补齐到其他语言）
- ✅ 检查冗余 key（列出模块/文件/语言/具体 key）
- ✅ 删除冗余 key（先报告再确认）
- ✅ doctor（检查配置 / i18n 目录 / OpenAI SDK / apiKey）
- ✅ init（生成/校验配置文件 `slang_i18n.yaml`）

> 适用前提：你的 i18n JSON 必须是 **key -> string** 的平铺结构（除 `@@locale` 外不允许嵌套或非字符串值）。

---

## 安装

该工具随工具集一起安装。安装完成后应可直接运行：

```sh
slang_i18n --help
```

依赖：

```sh
pip install pyyaml openai>=1.0.0
```

并确保项目内存在你的翻译模块：`slang_translate.py`，且导出：

- `translate_flat_dict`
- `OpenAIModel`
- `TranslationError`

---

## 目录约定

请在 Flutter 项目根目录执行，默认要求存在：

- `i18n/`
- `slang_i18n.yaml`

文件命名规则：

- 根目录：`i18n/en.i18n.json`, `i18n/ja.i18n.json` ...
- 子模块：`i18n/assets/assets_en.i18n.json`, `i18n/assets/assets_ja.i18n.json` ...

工具会把 `i18n/` 本身 + `i18n/` 下一层子目录都视为一个 group。

---

## 快速开始

### 1) 初始化配置

生成 `slang_i18n.yaml`（若已存在则校验，不覆盖）：

```sh
slang_i18n init
```

### 2) 环境检查

```sh
slang_i18n doctor
```

你也可以传入 key：

```sh
slang_i18n doctor --api-key sk-***
```

### 3) 进入交互菜单

```sh
slang_i18n
```

---

## 配置文件 slang_i18n.yaml

示例：

```yaml
source_locale: en
target_locales:
  - ja
  - zh-CN

# 可选：追加翻译规则（会追加到系统 base prompt 后）
prompt_en: |
  Translate UI strings naturally.
  Keep punctuation and placeholders unchanged.

options:
  sort_keys: true
  cleanup_extra_keys: true
  incremental_translate: true
```

字段说明：

- `source_locale`: 源语言（通常 `en`）
- `target_locales`: 目标语言列表
- `prompt_en`: 可选，额外的翻译规则（英文）
- `options.sort_keys`: 写回文件时是否排序 key
- `options.cleanup_extra_keys`: 翻译/写回时是否清理目标语言中多余 key
- `options.incremental_translate`: 交互默认的翻译模式（增量/全量）

---

## 交互菜单说明

运行 `slang_i18n` 后，会看到类似菜单：

```text
1 - 排序所有 i18n json（按 key，@@locale 保持在最前）
2 - 增量翻译（en 新增 key 自动补齐到其他语言）
3 - 检查冗余 key（仅报告，不删除）
4 - 删除冗余 key（先报告，再确认是否删除）
5 - doctor
6 - init
0 - 退出
```

### 1) 排序

- `@@locale` 永远放第一
- 其余 key 按字母序排列（受 `options.sort_keys` 控制）

### 2) 翻译（增量 / 全量）

- **增量**：只翻译源语言新增 key（目标文件不存在的 key）
- **全量**：按源语言全量重建（会覆盖已有同名 key 的值）

翻译前会检测：

- OpenAI SDK 是否可用
- 是否提供 apiKey（参数 `--api-key` 或环境变量 `OPENAI_API_KEY`）

### 3) 检查冗余 key

冗余定义：目标语言文件里存在、但源语言文件（通常 en）里不存在的 key。

报告会输出：

- module（group）
- locale
- 文件路径
- 具体冗余 key 列表

### 4) 删除冗余 key

执行流程：

1. 先做一次“检查冗余 key”并输出报告
2. 让你确认是否删除
3. 删除后按配置写回（可排序、保留 `@@locale`）

---

## 命令行参数

### --api-key

用于覆盖环境变量 `OPENAI_API_KEY`：

```sh
slang_i18n --api-key sk-***
```

### --model

指定翻译模型（默认 `OpenAIModel.GPT_4O`）：

```sh
slang_i18n --model gpt-4o
```

> 注意：这里的可用值取决于你 `slang_translate.OpenAIModel` 的枚举定义。

---

## 常见问题

### Q: 为什么我运行翻译时报 “仅支持平铺 string->string”？

A: 该工具只支持 flat JSON（key -> string）。如果你的 JSON 有嵌套对象、数组或非字符串值，需要先在生成阶段改成 flat 结构。

### Q: 为什么会自动创建缺失的语言文件？

A: 为了保证每个 group 都至少有一份 `source_locale` 与 `target_locales` 对应文件，缺失则会创建一个只包含 `@@locale` 的空壳文件，便于后续排序/翻译。

---

```

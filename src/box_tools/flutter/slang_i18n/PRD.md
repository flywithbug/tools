
---

# A. 需求文档（PRD）

## 1. 项目背景与目标

### 1.1 背景

Flutter 项目使用 `slang` / `slang_flutter` 管理多语言资源，随着项目增长，出现以下问题：

* 多语言 JSON 文件结构不统一
* 多业务模块下文件命名混乱
* 翻译过程重复、不可控
* 冗余 key 长期积累
* 语言风格无法统一约束
* 缺少 CI 可用的校验工具

### 1.2 目标

构建一个 **Flutter i18n 多语言资源管理与 AI 翻译 CLI 工具**，用于：

* 统一创建与维护 slang 兼容的多语言 JSON
* 强制结构规范（flat + `@@locale`）
* 支持单业务 / 多业务模块
* 提供增量翻译能力（基于 AI）
* 检测并清理冗余 key
* 支持 CI 校验（doctor / check）

---

## 2. 非目标（明确不做）

* ❌ 不生成 Dart / slang 代码
* ❌ 不修改 Dart 源码
* ❌ 不支持嵌套 JSON
* ❌ 不做 GUI
* ❌ 不自动重命名业务 key

---

## 3. 配置文件（slang_i18n.yaml）

### 3.1 配置结构（已冻结）

```yaml
i18nDir: i18n

source_locale:
  code: en
  name_en: English

target_locales:
  - code: zh_hans
    name_en: Simplified Chinese
  - code: zh_hant
    name_en: Traditional Chinese
  - code: ja
    name_en: Japanese

openAIModel: gpt-4o

prompt_by_locale:
  zh_hant: |
    Use Traditional Chinese.
    Prefer Taiwan-style UI wording.
  ja: |
    Use natural Japanese UI expressions.

options:
  sort_keys: true
  incremental_translate: true
  cleanup_extra_keys: true
  normalize_filenames: true
```

### 3.2 配置语义

* `source_locale`：**唯一权威语言**
* `target_locales`：只允许 source → target
* `prompt_by_locale`：翻译 system prompt 的附加约束
* `i18nDir`：多语言根目录

---

## 4. 强约束规则（不可违反）

### 4.1 JSON 必须平铺（Flat JSON Only）

**合法**

```json
{
  "@@locale": "en",
  "home_title": "Home"
}
```

**非法**

```json
{
  "home": { "title": "Home" }
}
```

规则：

* 顶层 object
* value 只能是 string
* 禁止 object / array / 任意嵌套

---

### 4.2 `@@locale` 元字段（强制）

* 每个 JSON 文件必须包含：

```json
"@@locale": "<locale_code>"
```

* 规则：

    * 顶层字段
    * 第一个 key
    * value 必须与文件名 locale 一致
* `@@locale`：

    * 不参与翻译
    * 不参与冗余检查
    * 不允许被删除

---

## 5. 目录结构与文件命名

### 5.1 模式判定

* `i18nDir` 下无子目录 → **单业务模式**
* `i18nDir` 下有子目录 → **多业务模式**

---

### 5.2 单业务模式

```text
i18n/
  en.json
  zh_hans.json
  ja.json
```

命名规则：

```
{{locale}}.json
```

---

### 5.3 多业务模式

```text
i18n/
  home/
  trade/
```

#### 命名推断规则

1. 扫描是否存在 `*_{{locale}}.json`
2. 若存在：

    * 使用已有 prefix
3. 若不存在：

    * 使用目录名作为 prefix

文件模板：

```
{{prefix}}_{{locale}}.json
```

#### 创建顺序（强制）

1. 创建 source locale 文件
2. 创建 target locale 文件

#### 冲突

* 同目录多个 prefix → doctor 报错
* 不自动猜测

---

## 6. CLI 功能需求

### CLI 菜单（冻结）

```
("1", "sort",      "排序"),
("2", "translate", "翻译（默认增量）"),
("3", "check",     "检查冗余"),
("4", "clean",     "删除冗余"),
("5", "doctor",    "环境诊断"),
("6", "init",      "生成/校验配置"),
("0", "exit"),
```

---

### 6.1 init

* 生成或校验配置文件
* 创建 i18nDir（可选）
* 创建 source locale 文件（含 `@@locale`）

---

### 6.2 sort

* `@@locale` 永远第一
* 其他 key 按字典序
* 若发现嵌套 JSON → 拒绝执行

---

### 6.3 translate

* 默认 **增量翻译**
* 排除 `@@locale`
* 使用 OpenAI 翻译底座
* 若发现嵌套 → 拒绝执行

---

### 6.4 check

* 冗余定义：

  ```
  target_keys - source_keys
  ```
* 排除 `@@locale`
* 发现嵌套：

    * 提示平铺
    * 返回失败状态（CI）

---

### 6.5 clean

* 删除冗余 key
* 删除前备份
* 永不删除 `@@locale`
* 删除后自动 sort

---

### 6.6 doctor

必须检查：

* 配置合法性
* 目录结构
* prefix 冲突
* JSON 是否平铺
* `@@locale` 是否存在
* `@@locale` 与文件名是否一致
* OpenAI API Key（如需翻译）

---

# B. 技术设计与架构文档（Tech Design）

## 1. 设计目标

* 与现有 `box_tools` 工具体系一致
* tool.py 保持“瘦”
* 核心规则集中
* 高可测试性
* 易于扩展（未来翻译引擎 / ICU）

---

## 2. 组件位置

```
box_tools/flutter/slang_i18n/
```

---

## 3. 目录结构（最终推荐）

```
slang_i18n/
├── README.md
├── __init__.py
├── tool.py                 # CLI 入口
├── models.py               # 纯数据模型
├── config.py               # 配置加载与校验
├── layout.py               # 目录扫描与 prefix 推断
├── json_ops.py             # JSON flat / @@locale / 排序
├── actions_core.py         # sort / check / clean / doctor / init
├── actions_translate.py    # translate（单独）
```

---

## 4. Model 层设计原则

* 只使用 `@dataclass`
* 不做 IO
* 不执行业务
* 三类模型：

    1. Config Model
    2. Layout / Group Model
    3. JsonFileState Model

---

## 5. Actions 拆分原则（已确认）

### actions_core.py

包含：

* run_init
* run_sort
* run_check
* run_clean
* run_doctor

特点：

* 不依赖 OpenAI
* 可用于 CI
* 低风险操作

---

### actions_translate.py

包含：

* run_translate

特点：

* 唯一调用 OpenAI
* 高风险、高成本
* 强前置校验

---

## 6. tool.py 职责边界

tool.py 只负责：

* BOX_TOOL 定义
* argparse
* 菜单交互
* action 分发

不负责：

* 目录扫描
* JSON 处理
* 翻译逻辑

---

## 7. 核心设计原则（冻结）

* **结构优先于翻译**
* **source locale 是唯一真理**
* **默认增量**
* **translate 必须隔离**
* **doctor / check 可作为 CI gate**
* **所有规则集中，不分散**

---

## 8. 当前状态

✅ 需求已完整
✅ 技术架构已确定
✅ 与现有工具体系完全对齐
✅ 可直接进入开发

---

### 下一步（你任选一个）

1. 👉 我直接给你 **`models.py` 的完整定义**
2. 👉 或生成 **`slang_i18n/tool.py` 的 CLI 骨架代码**
3. 👉 或先写 **layout 扫描 + prefix 推断的实现设计**


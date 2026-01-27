# strings_i18n 技术设计文档（开发向）

本文档用于 **指导 strings_i18n 的实现开发**，明确架构边界、核心流程、关键算法与约束条件。本文档以你已确认的 commands 与行为为唯一准绳，不引入额外功能。

---

## 1. 工具定位

**strings_i18n** 是一个面向 **iOS / Xcode `.strings` 本地化体系** 的 CLI 工具，目标是：

* 保持 `.strings` 文本的结构、注释、顺序稳定
* 以 `Base.lproj/Localizable.strings` 作为唯一权威来源
* 提供可预测、可回滚、可 CI 集成的多语言维护能力

工具不是“翻译平台”，而是 **工程整理 + 翻译执行器**。

---

## 2. 保留的 Commands（唯一功能集合）

```python
menu = [
    ("gen-l10n",         "生成 L10n.swift（按点号前缀分组）"),
    ("sort",             "排序 Localizable.strings（Base 分组/2空行/注释跟随；其他语言只排序）"),
    ("translate-core",   "翻译（core）：Base.lproj → core_locales"),
    ("translate-target", "翻译（target）：source_locale.lproj → target_locales"),
    ("doctor",           "环境诊断"),
    ("init",             "生成/校验配置"),
]
```

除以上 6 个 command 外，不再扩展功能入口。

---

## 3. 总体架构

### 3.1 模块拆分（同构 slang_i18n）

```text
strings_i18n/
  tool.py        # CLI 入口 / 参数解析 / action 路由
  data.py        # 配置、.strings IO、排序、L10n.swift 生成、doctor
  translate.py   # 翻译任务构建 + 并发翻译 + 写回（调用 data）
```

### 3.2 模块职责边界

* **tool.py**

    * 解析 CLI 参数
    * 校验 action 是否需要 config
    * 调用 data / translate 层
    * 控制 exit code

* **data.py**（核心模块）

    * 读取与校验 `strings_i18n.yaml`
    * Xcode 路径与 Base 文件扫描
    * `.strings` 文件解析 / 排序 / 写回（保注释）
    * 生成 `L10n.swift`
    * doctor 环境诊断

* **translate.py**

    * 构建“需要翻译”的任务队列
    * 并发调用翻译引擎
    * 将翻译结果合并回 `.strings`（通过 data.py）

---

## 4. 配置文件（strings_i18n.yaml）

### 4.1 约束原则

* **配置文件结构不可更改**
* 实现必须适配配置，而不是反向要求用户迁移配置

### 4.2 关键字段语义（实现需遵守）

* `lang_root`：语言目录根路径
* `base_folder`：Base 目录名（通常 `Base.lproj`）
* `base_locale[0]`：Base.lproj 的语言语义说明
* `source_locale[0]`：translate-target 的源语言
* `core_locales`：translate-core 的目标语言
* `target_locales`：translate-target 的目标语言
* `prompts`：翻译提示词（default + by_locale_en）
* `options.incremental_translate`：是否默认增量翻译

---

## 5. `.strings` 文件处理模型（data.py 核心）

### 5.1 解析模型

使用“保真解析”，而不是简单 KV：

```python
ParsedStrings:
  header: List[str]        # 文件头（未遇到 entry 前的内容）
  entries: List[Entry]    # 有序 entry 列表
  tail: List[str]          # 文件尾杂项

Entry:
  key: str
  value: str
  comments: List[str]     # 紧贴 key 的注释
  raw_before: List[str]   # entry 前的杂项行
```

### 5.2 写回原则

* 注释必须紧贴对应 key
* 不破坏 header / tail
* 排序必须 **幂等**（重复执行不产生 diff 抖动）

---

## 6. sort Command（核心整理流程）

### 6.1 sort 的职责

`sort` 是一个 **确定性整理操作**，不涉及翻译 API：

* 稳定 Base 的结构与顺序
* 让所有语言文件与 Base 顺序对齐

### 6.2 Base.lproj/Localizable.strings 排序规则

* 分组规则：

    * `prefix = key.split('.', 1)[0]`
* 组内排序：按 key 字典序
* 组间间隔：**2 个空行**
* 注释：必须紧贴对应 key

这是“权威排序规则”。

### 6.3 其它语言文件排序规则

* 顺序：完全跟随 Base 的 key 顺序
* 不做分组
* Base 中不存在的 key（extra）：

    * 若存在，仅追加在末尾（按 key 排序）
* 不插入额外空行

---

## 7. gen-l10n Command（生成 L10n.swift）

### 7.1 输入

* `Base.lproj/Localizable.strings`

### 7.2 输出规则

* Swift 文件：`L10n.swift`
* 分组：按点号前缀生成嵌套 enum
* 组内顺序：保持 Base 中的原始顺序
* 注释：

    * `.strings` 注释 → Swift doc comment

### 7.3 目标

* Swift 代码稳定
* diff 可读
* 不因重复生成而产生无意义改动

---

## 8. 翻译模块（translate.py）

### 8.1 翻译模式

* 默认：增量翻译（缺失或空值 key）
* `--full`：强制全量覆盖

### 8.2 translate-core

* 源：`Base.lproj/*.strings`
* 目标：`core_locales`

### 8.3 translate-target

* 源：`{source_locale}.lproj/*.strings`
* 目标：`target_locales`

### 8.4 翻译任务粒度

* 一个任务 = 一个 `.strings` 文件 + 一个目标语言
* 并发执行，主线程写回

---

## 9. doctor Command

### 9.1 检查项

* Python 依赖（PyYAML / openai SDK）
* `strings_i18n.yaml` 是否存在且合法
* `lang_root / base_folder` 是否存在
* `Base.lproj/Localizable.strings` 是否存在

### 9.2 目的

* 在任何 destructive 操作前提供安全网

---

## 10. init Command

### 10.1 行为

* 若配置不存在：

    * 生成带注释的 `strings_i18n.yaml` 模板
* 若配置存在：

    * 仅校验，不覆盖

---

## 11. 开发约束（必须遵守）

* 不改变现有配置 schema
* 不在 sort 中调用翻译 API
* 所有文件写操作必须支持 `--dry-run`
* 所有排序/生成操作必须幂等
* Base 是唯一权威来源

---

## 12. 结语

strings_i18n 的设计核心不是“多”，而是“稳”。

只要保证：

* Base 稳定
* 排序稳定
* 翻译可控

这个工具就可以长期服役，而不会演变成不可维护的脚本。

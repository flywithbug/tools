下面是**已经把「L10n.swift 生成能力」完整、正式地补充进去后的【最终版 PRD】**。
这是一份**可以直接作为 PR / RFC / 设计文档提交的版本**，内容已经与你当前所有约束**完全对齐**，没有模糊空间。

---

# PRD：strings_i18n

**iOS `.strings` 多语言翻译、治理与 Swift 代码生成工具**

---

## 1. 背景（Background）

iOS 项目使用 `.lproj/*.strings` 管理多语言文本。随着项目规模增长，常见问题包括：

* 多语言 key 不一致、缺失或冗余
* 手工翻译成本高、易遗漏
* `.strings` 文件排序混乱，diff 噪声大
* 注释在多语言中扩散或漂移，降低可维护性
* Swift 层通过字符串 key 访问本地化文本，缺乏类型安全

`strings_i18n` 旨在为 iOS 项目提供一套**工程化、确定性、可扩展**的多语言解决方案，并补齐 Swift 侧的类型安全访问能力。

---

## 2. 目标（Goals）

1. 自动化 iOS `.strings` 多语言翻译
2. 明确拆分翻译流水线：

    * **核心语言翻译**：`Base → core_locales`
    * **其他语言翻译**：`source_locale → target_locales`
3. 提供稳定、确定的 `.strings` 排序与分组规则
4. 明确职责边界：

    * **Base 文件承担“文档与语义说明”**
    * **非 Base 文件仅作为翻译结果**
5. 自动生成 `L10n.swift`，提供类型安全的本地化访问方式

---

## 3. 非目标（Non-Goals）

* `.stringsdict` 处理
* 自动修改 Xcode `project.pbxproj`
* 翻译记忆（TM）或术语库
* 非 `Localizable.strings` 的 Swift 代码生成（一期）

---

## 4. 核心设计原则（Design Principles）

1. **Base 是唯一真相源**

    * key 集合
    * 排序结构
    * 注释语义
2. **非 Base 文件是纯结果**

    * 不保留、不继承、不生成注释
3. **输出必须幂等**

    * 多次运行结果完全一致
4. **规则必须机械、无歧义**

    * 避免“人工理解注释含义”的实现

---

## 5. 配置文件：`strings_i18n.yaml`

### 5.1 职责

* 定义语言角色
* 定义目录结构
* 定义翻译、排序、清理规则
* 定义 Swift 代码生成行为

### 5.2 核心字段（节选）

```yaml
options:
  cleanup_extra_keys: true
  incremental_translate: true
  sort_keys: true
  normalize_filenames: true

languages: ./languages.json

lang_root: ios/Resources
base_folder: Base.lproj

base_locale:
  code: zh-Hans
  name_en: Chinese (Simplified)

source_locale:
  code: en
  name_en: English

core_locales:
  - code: zh-Hant
    name_en: Chinese (Traditional)

target_locales:
  # init 时生成

swift_codegen:
  enabled: true
  input_file: Localizable.strings
  output_file: ios/Sources/Generated/L10n.swift
  enum_name: L10n
```

---

## 6. 语言角色定义（Language Roles）

### 6.1 Base

* 路径：`Base.lproj/*.strings`
* 职责：

    * 定义完整 key 集合
    * 定义排序与分组结构
    * 承载全部注释

### 6.2 Source Locale

* 通常为 `en`
* 用于非核心语言翻译源

### 6.3 Core Locales

* 关键市场语言
* 翻译路径：`Base → core_locales`

### 6.4 Target Locales

* 其他语言
* 翻译路径：`source_locale → target_locales`

---

## 7. 翻译工作流（Translation Pipeline）

### 7.1 核心语言翻译

* 输入：`Base.lproj/*.strings`
* 输出：`<core>.lproj/*.strings`
* 支持增量 / 全量

### 7.2 其他语言翻译

* 输入：`<source>.lproj/*.strings`
* 输出：`<target>.lproj/*.strings`

---

## 8. `.strings` 排序与写回规范（Format Specification）

### 8.1 排序与分组规则（所有语言通用）

1. **按前缀分组**

    * 前缀定义：`key.split(".", 1)[0]`
2. **组内按 key 字母序排序**
3. **组与组之间插入一个空行**

---

### 8.2 Base 文件写回规则

* **保留所有 Base 中已有注释**
* 注释 **必须位于对应 key 或分组的上方**
* 允许存在：

    * 分组注释（如 `/* 通用 */`）
    * 条目注释
* 不允许：

    * 注释位于 key 下方
    * 注释在排序过程中丢失或漂移

#### 示例

```strings
/* 通用 */
"general.cancel" = "取消";
"general.close" = "关闭";
```

---

### 8.3 非 Base 文件写回规则（core / target）

* **不保留任何注释**
* 不继承 Base 注释
* 不保留自身历史注释
* 输出仅包含：

    * `"key" = "value";`
    * 分组空行

---

## 9. 冗余 Key 清理（cleanup_extra_keys）

* Base → core：Base 不存在但 core 存在的 key
* Source → target：Source 不存在但 target 存在的 key
* `cleanup_extra_keys: true` 时自动删除

---

## 10. CLI 命令

```bash
strings_i18n init
strings_i18n doctor
strings_i18n sort
strings_i18n translate
strings_i18n gen-swift
strings_i18n menu
```

---

## 11. doctor 命令职责

* 校验配置合法性
* 校验 `.lproj` 目录存在性
* 校验 `.strings`：

    * 缺 key
    * 冗余 key
    * 重复 key
    * 空 value

---

## 12. init 行为

* 生成 `strings_i18n.yaml`
* 从 `languages.json` 生成 `target_locales`
* 保留模板注释，仅替换必要字段块

---

## 13. Swift 代码生成：`L10n.swift`

### 13.1 功能目标

生成类型安全的 Swift API，用于访问 `Localizable.strings` 中的本地化文本。

---

### 13.2 输入与范围

* **仅使用**：

    * `Base.lproj/Localizable.strings`
* 不处理其他 `.strings` 文件（一期）

---

### 13.3 生成结构规范

#### 文件头

```swift
// Auto-generated from Base.lproj/Localizable.strings
import Foundation
```

#### 工具扩展

```swift
extension String {
  func callAsFunction(_ args: CVarArg...) -> String {
    String(format: self, arguments: args)
  }
}
```

#### 命名空间

```swift
enum L10n {
  enum Alert {
    static var titleTip: String {
      NSLocalizedString("alert.title.tip", value: "提示", comment: "提示")
    }
  }
}
```

---

### 13.4 key → Swift 标识符规则

* 前缀作为子 `enum`
* 剩余部分：

    * `.`、`_` 分词
    * camelCase
* 必须生成合法 Swift 标识符
* 命名冲突需使用稳定、确定的消歧策略

---

### 13.5 Base 注释保留规则（Swift）

* **仅使用 Base 中的注释**
* `.strings` 中的 `/* ... */`：

    * 若为分组注释 → 生成到对应 `enum` 上方
    * 若为条目注释 → 生成到对应 `static var` 上方
* 生成形式：

```swift
/// 通用
enum General {
  /// 关闭按钮
  static var close: String { ... }
}
```

---

### 13.6 NSLocalizedString 参数规则

* `key`：原始 key
* `value`：Base 中的 value
* `comment`：Base 中的 value（与示例一致）

---

## 14. 验收标准（Acceptance Criteria）

1. Base `.strings` 注释完整保留、位置稳定
2. 非 Base `.strings` 不包含任何注释
3. 所有 `.strings` 输出顺序一致
4. `L10n.swift` 仅由 Base 的 `Localizable.strings` 生成
5. Swift 文件中保留 Base 注释
6. 多次运行结果幂等

---

## 15. 总结

`strings_i18n` 是一个**以工程稳定性优先**的 iOS 多语言系统：

* Base：**给人读**
* 其他语言：**给机器用**
* Swift API：**给编译器兜底**
* 排序、结构、确定性优先于翻译本身

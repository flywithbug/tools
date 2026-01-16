# slang_i18n

Flutter **slang** i18n 辅助工具（**flat `.i18n.json`，不支持嵌套**）

用于统一管理、校验、排序、清理和**增量翻译** slang 的多语言 JSON 文件，支持模块化目录结构，适合中大型 Flutter 项目。

---

## ✨ 核心能力

- 📁 **模块级 i18n 管理**
    - 支持 `i18n/<module>/*.i18n.json`
    - 若 `i18n/` 下存在子目录，则**不处理根目录 json**

- 🔤 **文件名规范化（可选）**
    - 自动规范为 `{folder}_{locale}.i18n.json`
    - 基于配置里的 locale 列表精确匹配（不会误伤 `zh_Hant`）
    - 不覆盖已有目标文件

- 🧹 **排序**
    - `@@locale` 永远第一
    - 其他 `@@meta` 紧随其后
    - 普通 key 按字母排序（可配置）

- 🔍 **冗余 key 检查 / 清理**
    - 仅对“普通 key”生效（不包含 `@@meta`）
    - 报告所属模块、语言、文件路径
    - 删除前二次确认（CLI / 交互模式）

- 🌍 **增量 / 全量翻译**
    - 默认 **增量翻译**（只翻译 en 中新增的 key）
    - 可切换为全量翻译
    - **group / locale 级进度显示**（百分比 + ETA）

- 🧠 **slang 语义感知**
    - 所有 `@@xxx` key 视为 metadata：
        - 不翻译
        - 不参与冗余判断
        - value 允许 bool / number / object / list

---

## 📦 目录结构示例

```text
i18n/
├── assets/
│   ├── assets_en.i18n.json
│   ├── assets_zh_Hant.i18n.json
│   └── assets_ja.i18n.json
├── settings/
│   ├── settings_en.i18n.json
│   └── settings_fr.i18n.json
```

---

## 🛠️ 安装依赖

```bash
pip install openai>=1.0.0 pyyaml
```

---

## 🔐 OpenAI API Key

```bash
export OPENAI_API_KEY="sk-xxxxxxxx"
```

---

## 🚀 快速开始

```bash
slang_i18n init
slang_i18n doctor
slang_i18n
```

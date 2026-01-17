# `strings_i18n` 工具使用指南

## 配置文件 `strings_i18n.yaml`

`strings_i18n.yaml` 配置文件定义了以下内容：

- **baseLocale**: 默认语言（例如 `zh_hans`）。
- **coreLocales**: 核心语言的列表（例如 `["en", "zh_Hant", "zh_Hans", "ja", "ko", "yue"]`）。
- **sourceLocale**: 源语言，默认为 `en`（英语）。

```yaml
baseLocale: zh_hans
coreLocales:
  - en
  - zh_Hant
  - zh_Hans
  - ja
  - ko
  - yue
sourceLocale: en
```

## 功能

1. **全量翻译**：
   - 核心语言的全量翻译：翻译核心语言。
   - 非核心语言的全量翻译：翻译非核心语言。

2. **增量翻译**：
   - 仅翻译缺失的部分，不覆盖已有的翻译。

3. **冗余字段删除**：
   - 检查冗余字段：列出在 `baseLocale` 中缺失，但在其他语言中存在的字段，用户可以选择删除这些冗余字段。

4. **排序**：
   - 对所有语言文件按键名进行排序，保留原有注释。

## 使用示例

### 增量翻译

```bash
python strings_i18n.py --source en --coreLocales en,zh_Hant,zh_Hans --baseLocale zh_Hans --fullTranslation false
```

### 全量翻译

```bash
python strings_i18n.py --source en --coreLocales en,zh_Hant,zh_Hans --baseLocale zh_Hans --fullTranslation true
```

### 删除冗余字段

```bash
python strings_i18n.py --source en --coreLocales en,zh_Hant,zh_Hans --baseLocale zh_Hans --removeRedundant true
```

### 排序语言文件

```bash
python strings_i18n.py --source en --coreLocales en,zh_Hant,zh_Hans --baseLocale zh_Hans --sort true
```

---

## 注意事项

- **增量翻译**：将只翻译缺失的翻译，不覆盖已存在的翻译内容。
- **全量翻译**：对指定语言进行全量翻译，覆盖所有字段。
- **冗余字段**：如果在某个语言中存在但在 `baseLocale` 中没有的字段，可以选择删除这些冗余字段。


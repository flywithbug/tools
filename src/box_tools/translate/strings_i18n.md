
# strings_i18n 工具文档

`strings_i18n` 是一个 iOS 项目中用于处理多语言翻译和本地化的工具。

## 功能概述
- 生成 `L10n.swift` 文件，按点号前缀分组。
- 对 `Base.lproj/Localizable.strings` 进行排序（分组 & 组间保留两空行，注释跟随）。
- 对所有语言的 `Localizable.strings` 文件进行排序，参照 `Base.lproj` 文件的顺序。
- 增量翻译和全量翻译：
  - 增量翻译：翻译缺失的翻译内容，排除核心语言集。
  - 全量翻译：强制翻译所有内容，排除核心语言集。

## 配置文件 `strings_i18n.yaml`

该工具支持 YAML 配置文件来指定翻译行为。配置文件的结构如下：

```yaml
primarySourceLocale: en
primaryTargetLocales:
  - zh_Hant
  - ja
  - ko
secondarySourceLocale: zh_Hans
secondaryTargetLocales:
  - en
  - zh_Hant
  - zh-HK
  - ja
  - ko
coreLocales:
  - en
  - zh_Hant
  - zh-Hans
```

## 使用方法

1. **初始化配置**：

   运行以下命令生成默认配置：

   ```sh
   python3 strings_i18n.py init
   ```

2. **翻译字符串**：

   运行以下命令执行翻译：

   ```sh
   python3 strings_i18n.py translate
   ```

3. **生成 `L10n.swift` 文件**：

   在翻译完成后，运行以下命令生成 `L10n.swift` 文件：

   ```sh
   python3 strings_i18n.py generate_l10n
   ```

4. **全量翻译**：

   若需要全量翻译，可以通过命令行参数指定：

   ```sh
   python3 strings_i18n.py translate --full
   ```

5. **检查冗余翻译**：

   工具支持检查冗余翻译并删除多余的翻译。执行以下命令：

   ```sh
   python3 strings_i18n.py clean --yes
   ```

## 常见问题

1. **如何修改配置文件？**

   配置文件 `strings_i18n.yaml` 可以根据项目需求修改，调整语言设置和翻译策略。

2. **如何使用 OpenAI 翻译？**

   需要提供有效的 OpenAI API 密钥，并通过 `OPENAI_API_KEY` 环境变量或配置文件提供。

3. **如何更新 `Localizable.strings` 文件？**

   更新后的文件会自动保存至指定的目标目录。

## License

MIT

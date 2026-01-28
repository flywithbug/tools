# tools

一个用于日常开发/发布的小工具集（Python 脚本工具库）。

## 安装

使用 pipx 从 GitHub 安装/强制覆盖安装：

```bash
pipx install --force "git+https://github.com/flywithbug/tools.git"
```


## 工具集介绍

## 目录

- [工具总览](#section)
- [工具集文档索引](#section)
- [box（工具集管理）](#box)
  - [`box`](#box-tool)
- [ai/chat](#ai-chat)
  - [`box_ai_chat`](#box_tools-ai-chat-tool)
- [ai/file](#ai-file)
  - [`box_ai_file`](#box_tools-ai-file-tool)
- [ai/translate](#ai-translate)
  - [`box_ai_translate`](#box_tools-ai-translate-tool)
- [flutter/pubspec](#flutter-pubspec)
  - [`box_pubspec`](#box_tools-flutter-pubspec-tool)
- [flutter/riverpod_gen](#flutter-riverpod_gen)
  - [`box_riverpod_gen`](#box_tools-flutter-riverpod_gen-tool)
- [flutter/slang_i18n](#flutter-slang_i18n)
  - [`box_slang_i18n`](#box_tools-flutter-slang_i18n-tool)
- [gpt/json](#gpt-json)
  - [`box_json_i18n`](#box_tools-gpt-json-tool)
- [iOS/strings_i18n](#ios-strings_i18n)
  - [`box_strings_i18n`](#box_tools-ios-strings_i18n-tool)

---

<a id="section"></a>

## 工具总览

### box（工具集管理）

- **[`box`](#box-tool)**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表（[README.md](src/box/README.md)）

### ai/chat

- **[`box_ai_chat`](#box_tools-ai-chat-tool)**：命令行连续对话：输入问题→等待 AI 回复→继续追问（支持 /new /reset /save /load /model 等）（[README.md](src/box_tools/ai/chat/README.md)）

### ai/file

- **[`box_ai_file`](#box_tools-ai-file-tool)**：交互式多语言翻译：选择源语言/目标语言后输入文本，AI 实时翻译（支持中途切换）（[README.md](src/box_tools/ai/file/README.md)）

### ai/translate

- **[`box_ai_translate`](#box_tools-ai-translate-tool)**：交互式多语言翻译：选择源语言/目标语言后输入文本，AI 实时翻译（支持中途切换）（[README.md](src/box_tools/ai/translate/README.md)）

### flutter/pubspec

- **[`box_pubspec`](#box_tools-flutter-pubspec-tool)**：Flutter pubspec.yaml 管理 CLI：支持 version 升级（patch/minor）、依赖升级（基于 flutter pub outdated --json 的计划/执行）、依赖发布（flutter pub publish / dry-run），以及 doctor 本地检查。修改 pubspec.yaml 时只做最小必要的文本级局部替换，保留原有注释与结构。启动时会自动执行 doctor：无问题静默，有问题中断并输出错误。（[README.md](src/box_tools/flutter/pubspec/README.md)）

### flutter/riverpod_gen

- **[`box_riverpod_gen`](#box_tools-flutter-riverpod_gen-tool)**：生成 Riverpod StateNotifier + State 模板文件（notifier/state）（[README.md](src/box_tools/flutter/riverpod_gen/README.md)）

### flutter/slang_i18n

- **[`box_slang_i18n`](#box_tools-flutter-slang_i18n-tool)**：Flutter slang i18n 资源管理 CLI：基于默认模板生成/校验配置（保留注释），支持 sort/doctor，以及 AI 增量翻译（translate）（[README.md](src/box_tools/flutter/slang_i18n/README.md)）

### gpt/json

- **[`box_json_i18n`](#box_tools-gpt-json-tool)**：JSON i18n 资源管理 CLI：init/sync/sort/doctor/translate（启动默认 doctor）（[README.md](src/box_tools/gpt/json/README.md)）

### iOS/strings_i18n

- **[`box_strings_i18n`](#box_tools-ios-strings_i18n-tool)**：iOS .strings i18n 资源管理 CLI（骨架）：生成/校验配置（保留注释），支持 doctor/sort，以及 AI 翻译入口（translate，待实现）（[README.md](src/box_tools/iOS/strings_i18n/README.md)）

---

<a id="section"></a>

## 工具集文档索引

### box（工具集管理）

- **box**：[README.md](src/box/README.md)

### ai/chat

- **box_ai_chat**：[README.md](src/box_tools/ai/chat/README.md)

### ai/file

- **box_ai_file**：[README.md](src/box_tools/ai/file/README.md)

### ai/translate

- **box_ai_translate**：[README.md](src/box_tools/ai/translate/README.md)

### flutter/pubspec

- **box_pubspec**：[README.md](src/box_tools/flutter/pubspec/README.md)

### flutter/riverpod_gen

- **box_riverpod_gen**：[README.md](src/box_tools/flutter/riverpod_gen/README.md)

### flutter/slang_i18n

- **box_slang_i18n**：[README.md](src/box_tools/flutter/slang_i18n/README.md)

### gpt/json

- **box_json_i18n**：[README.md](src/box_tools/gpt/json/README.md)

### iOS/strings_i18n

- **box_strings_i18n**：[README.md](src/box_tools/iOS/strings_i18n/README.md)

---

<a id="box-tool"></a>

## box（工具集管理）

**简介**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表

**命令**：`box`

**用法**

```bash
box --help
box help
box doctor
box update
box version
box uninstall
box tools
box tools --full
```

**参数说明**

- `--help`：显示帮助（等同 box help）
- `tools --full`：显示工具的详细信息（options/examples），并显示导入失败原因

**示例**

- `box doctor`：诊断环境（python/pipx/PATH）
- `box update`：更新工具集
- `box tools`：列出当前工具与简介

**文档**

[README.md](src/box/README.md)

---

<a id="ai-chat"></a>

## ai/chat

<a id="box_tools-ai-chat-tool"></a>

### box_ai_chat

**简介**：命令行连续对话：输入问题→等待 AI 回复→继续追问（支持 /new /reset /save /load /model 等）

**命令**：`box_ai_chat`

**用法**

```bash
box_ai_chat
box_ai_chat --model gpt-4o-mini
box_ai_chat --system "You are a helpful assistant."
box_ai_chat --load ~/.box_tools/ai_chat/20260121_120000.json
```

**参数说明**

- `--model`：指定模型（默认 gpt-4o-mini，如 gpt-4o / gpt-4.1 / gpt-4.1-mini）
- `--system`：设置 system prompt（对话角色/风格）
- `--temperature`：采样温度（默认 0.2；越低越稳定）
- `--top-p`：top_p（默认 1.0）
- `--timeout`：请求超时（秒，默认 30）
- `--api-key`：显式传入 OpenAI API Key（不传则读取 OPENAI_API_KEY）
- `--load`：启动时加载会话文件（JSON）
- `--session`：指定 session id（用于固定默认保存文件名）
- `--store-dir`：会话保存目录（默认 ~/.box_tools/ai_chat）

**示例**

- `export OPENAI_API_KEY='sk-***' && box_ai_chat`：进入连续对话模式
- `box_ai_chat --model gpt-4o-mini`：用指定模型聊天
- `box_ai_chat --system "You are a senior iOS engineer."`：用自定义 system prompt 进入对话
- `box_ai_chat --load ~/.box_tools/ai_chat/20260121_120000.json`：加载历史会话继续聊

**文档**

[README.md](src/box_tools/ai/chat/README.md)

---

<a id="ai-file"></a>

## ai/file

<a id="box_tools-ai-file-tool"></a>

### box_ai_file

**简介**：交互式多语言翻译：选择源语言/目标语言后输入文本，AI 实时翻译（支持中途切换）

**命令**：`box_ai_file`

**用法**

```bash
box_ai_file
box_ai_file --model gpt-4o-mini
box_ai_file --source en --target zh-Hant --in en.json --out zh-hant.json
box_ai_file --in Base.lproj/Localizable.strings --out zh-Hant.lproj/Localizable.strings
```

**参数说明**

- `--model`：指定模型（默认 gpt-4o-mini）
- `--api-key`：显式传入 OpenAI API Key（不传则读取 OPENAI_API_KEY）
- `--source`：源语言代码（如 en/zh-Hans/ja…；不传则交互选择）
- `--target`：目标语言代码（如 zh-Hant/ko/fr…；不传则交互选择）
- `--in`：源文件路径（支持相对路径；不传则交互输入）
- `--out`：目标文件路径（支持相对路径；不传则交互输入）
- `--batch-size`：每批翻译条数（默认 40）
- `--no-pre-sort`：翻译前不做排序（默认会对源/目标做排序以稳定输出）

**示例**

- `export OPENAI_API_KEY='sk-***' && box_ai_file`：交互选择语言并输入文件路径
- `box_ai_file --source en --target zh-Hant --in en.json --out zh-hant.json`：英->繁中，翻译 json 文件
- `box_ai_file --in Base.lproj/Localizable.strings --out zh-Hant.lproj/Localizable.strings`：翻译 iOS .strings 文件

**文档**

[README.md](src/box_tools/ai/file/README.md)

---

<a id="ai-translate"></a>

## ai/translate

<a id="box_tools-ai-translate-tool"></a>

### box_ai_translate

**简介**：交互式多语言翻译：选择源语言/目标语言后输入文本，AI 实时翻译（支持中途切换）

**命令**：`box_ai_translate`

**用法**

```bash
box_ai_translate
box_ai_translate --model gpt-4o-mini
box_ai_translate --source en --target zh-Hans
```

**参数说明**

- `--model`：指定模型（默认 gpt-4o-mini）
- `--api-key`：显式传入 OpenAI API Key（不传则读取 OPENAI_API_KEY）
- `--source`：源语言代码（如 en/zh-Hans/ja…；不传则交互选择）
- `--target`：目标语言代码（如 zh-Hant/ko/fr…；不传则交互选择）

**示例**

- `export OPENAI_API_KEY='sk-***' && box_ai_translate`：进入翻译模式并交互选择语言
- `box_ai_translate --source en --target zh-Hans`：跳过选项表，直接英->简中

**文档**

[README.md](src/box_tools/ai/translate/README.md)

---

<a id="flutter-pubspec"></a>

## flutter/pubspec

<a id="box_tools-flutter-pubspec-tool"></a>

### box_pubspec

**简介**：Flutter pubspec.yaml 管理 CLI：支持 version 升级（patch/minor）、依赖升级（基于 flutter pub outdated --json 的计划/执行）、依赖发布（flutter pub publish / dry-run），以及 doctor 本地检查。修改 pubspec.yaml 时只做最小必要的文本级局部替换，保留原有注释与结构。启动时会自动执行 doctor：无问题静默，有问题中断并输出错误。

**命令**：`box_pubspec`

**用法**

```bash
box_pubspec
box_pubspec upgrade
box_pubspec publish
box_pubspec version
box_pubspec doctor
box_pubspec upgrade --yes
box_pubspec upgrade --outdated-json outdated.json
box_pubspec --project-root path/to/project
box_pubspec --box_pubspec path/to/pubspec.yaml doctor
```

**参数说明**

- `command`：子命令：menu/upgrade/publish/version/doctor（默认 menu）
- `--project-root`：项目根目录（默认当前目录）
- `--box_pubspec`：pubspec.yaml 路径（默认 project-root/pubspec.yaml）
- `--outdated-json`：指定 flutter pub outdated --json 的输出文件（可选，用于离线/复用）
- `--dry-run`：只打印计划/预览，不写入文件，不执行危险操作
- `--yes`：跳过所有确认（适合 CI/脚本）
- `--no-interactive`：关闭交互菜单（脚本模式）
- `--mode`：version：show/patch/minor（脚本模式快捷入口）

**示例**

- `box_pubspec`：进入交互菜单（启动时自动 doctor；无问题不输出）
- `box_pubspec doctor`：手动运行 doctor（会输出详细检查结果）
- `box_pubspec upgrade`：执行依赖升级（默认直接 apply + pub get + analyze + 自动提交）
- `box_pubspec upgrade --outdated-json outdated.json`：使用已有 outdated.json
- `box_pubspec upgrade --yes`：无交互执行升级
- `box_pubspec version --mode patch --yes`：补丁版本自增并直接写入（只改 version 行）

**文档**

[README.md](src/box_tools/flutter/pubspec/README.md)

---

<a id="flutter-riverpod_gen"></a>

## flutter/riverpod_gen

<a id="box_tools-flutter-riverpod_gen-tool"></a>

### box_riverpod_gen

**简介**：生成 Riverpod StateNotifier + State 模板文件（notifier/state）

**命令**：`box_riverpod_gen`

**用法**

```bash
box_riverpod_gen
box_riverpod_gen Product
box_riverpod_gen product_item --out lib/features/product
box_riverpod_gen Product --force
box_riverpod_gen Product --no-copywith
box_riverpod_gen Product --legacy
```

**参数说明**

- `--out`：输出目录（默认当前目录）
- `--force`：覆盖已存在文件
- `--no-copywith`：不生成 copy_with_extension 注解与 part '*.g.dart'
- `--legacy`：notifier 使用 flutter_riverpod/legacy.dart（默认启用 legacy）
- `--modern`：notifier 使用 flutter_riverpod/flutter_riverpod.dart

**示例**

- `box_riverpod_gen`：交互输入类名与输出目录
- `box_riverpod_gen Product`：在当前目录生成 product_notifier.dart 与 product_state.c.dart
- `box_riverpod_gen product_item --out lib/features/product`：在指定目录生成 product_item_* 文件
- `box_riverpod_gen Product --force`：覆盖已存在文件

**文档**

[README.md](src/box_tools/flutter/riverpod_gen/README.md)

---

<a id="flutter-slang_i18n"></a>

## flutter/slang_i18n

<a id="box_tools-flutter-slang_i18n-tool"></a>

### box_slang_i18n

**简介**：Flutter slang i18n 资源管理 CLI：基于默认模板生成/校验配置（保留注释），支持 sort/doctor，以及 AI 增量翻译（translate）

**命令**：`box_slang_i18n`

**用法**

```bash
box_slang_i18n
box_slang_i18n init
box_slang_i18n sort
box_slang_i18n doctor
box_slang_i18n translate
box_slang_i18n translate --no-incremental
box_slang_i18n --config slang_i18n.yaml
box_slang_i18n --project-root path/to/project
```

**参数说明**

- `command`：子命令：menu/init/sort/translate/doctor（默认 menu）
- `--config`：配置文件路径（默认 slang_i18n.yaml，基于 project-root）
- `--project-root`：项目根目录（默认当前目录）
- `--i18n-dir`：覆盖配置中的 i18nDir（相对 project-root 或绝对路径）
- `--no-incremental`：translate：关闭增量翻译，改为全量翻译

**示例**

- `box_slang_i18n init`：生成/校验配置文件（保留模板注释），并确保 languages.json 存在，同时创建 i18nDir
- `box_slang_i18n`：进入交互菜单（启动会优先校验配置 + 检查 i18nDir 目录）
- `box_slang_i18n sort`：对 i18n JSON 执行排序（按工具规则）
- `box_slang_i18n doctor`：环境/结构诊断：配置合法、目录结构、文件命名、@@locale/flat 等
- `box_slang_i18n translate`：AI 增量翻译：只翻译缺失 key（排除 @@locale）
- `box_slang_i18n translate --no-incremental`：AI 全量翻译：按 source 覆盖生成 target 的翻译内容
- `box_slang_i18n --project-root ./app --config slang_i18n.yaml init`：在指定项目根目录下初始化

**文档**

[README.md](src/box_tools/flutter/slang_i18n/README.md)

---

<a id="gpt-json"></a>

## gpt/json

<a id="box_tools-gpt-json-tool"></a>

### box_json_i18n

**简介**：JSON i18n 资源管理 CLI：init/sync/sort/doctor/translate（启动默认 doctor）

**命令**：`box_json_i18n`

**用法**

```bash
box_json_i18n
box_json_i18n init
box_json_i18n sync
box_json_i18n sort
box_json_i18n doctor
box_json_i18n translate
box_json_i18n translate --no-incremental
box_json_i18n --config gpt_json.yaml
box_json_i18n --project-root path/to/project
```

**参数说明**

- `command`：子命令：menu/init/sync/sort/translate/doctor（默认 menu）
- `--config`：配置文件路径（默认 gpt_json.yaml）
- `--project-root`：项目根目录（默认当前目录）
- `--i18n-dir`：覆盖配置中的 i18nDir（相对 project-root 或绝对路径）
- `--yes`：sync/sort：自动执行创建/删除等操作（跳过交互确认）
- `--no-incremental`：translate：关闭增量翻译，改为全量翻译
- `--skip-doctor`：跳过启动时默认 doctor（不建议）

**示例**

- `box_json_i18n init`：使用同目录模板 gpt_json.yaml 初始化/校验配置
- `box_json_i18n sort`：自动先 sync，再执行 sort
- `box_json_i18n translate`：目标目录/文件缺失会自动创建后再翻译

**文档**

[README.md](src/box_tools/gpt/json/README.md)

---

<a id="ios-strings_i18n"></a>

## iOS/strings_i18n

<a id="box_tools-ios-strings_i18n-tool"></a>

### box_strings_i18n

**简介**：iOS .strings i18n 资源管理 CLI（骨架）：生成/校验配置（保留注释），支持 doctor/sort，以及 AI 翻译入口（translate，待实现）

**命令**：`box_strings_i18n`

**用法**

```bash
box_strings_i18n
box_strings_i18n init
box_strings_i18n sort
box_strings_i18n doctor
box_strings_i18n translate
box_strings_i18n translate --no-incremental
box_strings_i18n --config strings_i18n.yaml
box_strings_i18n --project-root path/to/project
```

**参数说明**

- `command`：子命令：menu/init/sort/translate/doctor（默认 menu）
- `--config`：配置文件路径（默认 strings_i18n.yaml，基于 project-root）
- `--project-root`：项目根目录（默认当前目录）
- `--no-incremental`：translate：关闭增量翻译，改为全量翻译

**示例**

- `box_strings_i18n init`：生成/校验配置文件（保留模板注释），并从本地 languages.json 读取 target_locales，同时确保 lang_root 目录存在
- `box_strings_i18n`：进入交互菜单（启动会优先校验配置 + 基础目录结构）
- `box_strings_i18n doctor`：环境/结构诊断（骨架：路径与 Base.lproj 检查）
- `box_strings_i18n sort`：排序（骨架：待实现 .strings key 排序与写回）
- `box_strings_i18n translate`：翻译入口（骨架：待实现）

**文档**

[README.md](src/box_tools/iOS/strings_i18n/README.md)

---


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
- [ai/translate](#ai-translate)
  - [`box_ai_translate`](#box_tools-ai-translate-tool)
- [flutter/pub_publish](#flutter-pub_publish)
  - [`box_pub_publish`](#box_tools-flutter-pub_publish-tool)
- [flutter/pub_upgrade](#flutter-pub_upgrade)
  - [`box_pub_upgrade`](#box_tools-flutter-pub_upgrade-tool)
- [flutter/pub_version](#flutter-pub_version)
  - [`box_pub_version`](#box_tools-flutter-pub_version-tool)
- [flutter/riverpod_gen](#flutter-riverpod_gen)
  - [`box_riverpod_gen`](#box_tools-flutter-riverpod_gen-tool)
- [flutter/slang_i18n](#flutter-slang_i18n)
  - [`box_slang_i18n`](#box_tools-flutter-slang_i18n-tool)

---

<a id="section"></a>

## 工具总览

### box（工具集管理）

- **[`box`](#box-tool)**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表（[README.md](src/box/README.md)）

### ai/chat

- **[`box_ai_chat`](#box_tools-ai-chat-tool)**：命令行连续对话：输入问题→等待 AI 回复→继续追问（支持 /new /reset /save /load /model 等）（[README.md](src/box_tools/ai/chat/README.md)）

### ai/translate

- **[`box_ai_translate`](#box_tools-ai-translate-tool)**：交互式多语言翻译：选择源语言/目标语言后输入文本，AI 实时翻译（支持中途切换）（[README.md](src/box_tools/ai/translate/README.md)）

### flutter/pub_publish

- **[`box_pub_publish`](#box_tools-flutter-pub_publish-tool)**：自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，发布前检查（可交互处理 warning/info），提交并发布（支持 release 分支规则）（文档缺失：`src/box_tools/flutter/pub_publish/README.md`）

### flutter/pub_upgrade

- **[`box_pub_upgrade`](#box_tools-flutter-pub_upgrade-tool)**：升级 pubspec.yaml 中的私有 hosted/url 依赖（比对清单 + 确认；升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）（[README.md](src/box_tools/flutter/pub_upgrade/README.md)）

### flutter/pub_version

- **[`box_pub_version`](#box_tools-flutter-pub_version-tool)**：升级 Flutter pubspec.yaml 的 version（支持交互选择 minor/patch，可选 git 提交）（[README.md](src/box_tools/flutter/pub_version/README.md)）

### flutter/riverpod_gen

- **[`box_riverpod_gen`](#box_tools-flutter-riverpod_gen-tool)**：生成 Riverpod StateNotifier + State 模板文件（notifier/state）（[README.md](src/box_tools/flutter/riverpod_gen/README.md)）

### flutter/slang_i18n

- **[`box_slang_i18n`](#box_tools-flutter-slang_i18n-tool)**：Flutter slang i18n 资源管理 CLI：基于默认模板生成/校验配置（保留注释），支持 sort/doctor，以及 AI 增量翻译（translate）（[README.md](src/box_tools/flutter/slang_i18n/README.md)）

---

<a id="section"></a>

## 工具集文档索引

### box（工具集管理）

- **box**：[README.md](src/box/README.md)

### ai/chat

- **box_ai_chat**：[README.md](src/box_tools/ai/chat/README.md)

### ai/translate

- **box_ai_translate**：[README.md](src/box_tools/ai/translate/README.md)

### flutter/pub_publish

- **box_pub_publish**：未找到文档 `src/box_tools/flutter/pub_publish/README.md`（请创建该文件或在 BOX_TOOL['docs'] 指定）

### flutter/pub_upgrade

- **box_pub_upgrade**：[README.md](src/box_tools/flutter/pub_upgrade/README.md)

### flutter/pub_version

- **box_pub_version**：[README.md](src/box_tools/flutter/pub_version/README.md)

### flutter/riverpod_gen

- **box_riverpod_gen**：[README.md](src/box_tools/flutter/riverpod_gen/README.md)

### flutter/slang_i18n

- **box_slang_i18n**：[README.md](src/box_tools/flutter/slang_i18n/README.md)

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

<a id="flutter-pub_publish"></a>

## flutter/pub_publish

<a id="box_tools-flutter-pub_publish-tool"></a>

### box_pub_publish

**简介**：自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，发布前检查（可交互处理 warning/info），提交并发布（支持 release 分支规则）

**命令**：`box_pub_publish`

**用法**

```bash
box_pub_publish --msg fix crash on iOS
box_pub_publish --msg feat add new api --no-publish
box_pub_publish --pubspec path/to/pubspec.yaml --changelog path/to/CHANGELOG.md --msg release notes
box_pub_publish --msg hotfix --dry-run
box_pub_publish --msg release notes --yes-warnings
```

**参数说明**

- `--pubspec`：pubspec.yaml 路径（默认 ./pubspec.yaml）
- `--changelog`：CHANGELOG.md 路径（默认 ./CHANGELOG.md）
- `--msg`：更新说明（必填；可写多段，不需要引号）
- `--no-pull`：跳过 git pull
- `--no-git`：跳过 git add/commit/push（若不是 git 仓库也会自动跳过）
- `--no-publish`：跳过 flutter pub publish
- `--skip-pub-get`：跳过 flutter pub get
- `--skip-checks`：跳过发布前检查（flutter analyze + git 变更白名单）
- `--yes-warnings`：发布检查出现 issue（info/warning）时仍继续提交并发布（非交互/CI 推荐）
- `--dry-run`：仅打印将执行的操作，不改文件、不跑命令

**示例**

- `box_pub_publish --msg fix null error`：拉代码→升级版本→更新 changelog→pub get→检查(可交互)→提交→发布
- `box_pub_publish --msg release notes --no-publish`：只提交不发布
- `box_pub_publish --msg release notes --yes-warnings`：检查有 issue 也自动继续提交并发布（适合 CI）
- `box_pub_publish --msg try --dry-run`：预演一次，不做任何修改

**文档**

- 未找到文档：`src/box_tools/flutter/pub_publish/README.md`（请创建该文件）

---

<a id="flutter-pub_upgrade"></a>

## flutter/pub_upgrade

<a id="box_tools-flutter-pub_upgrade-tool"></a>

### box_pub_upgrade

**简介**：升级 pubspec.yaml 中的私有 hosted/url 依赖（比对清单 + 确认；升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）

**命令**：`box_pub_upgrade`

**用法**

```bash
box_pub_upgrade
box_pub_upgrade --yes
box_pub_upgrade --no-commit
box_pub_upgrade --private-host dart.cloudsmith.io
box_pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo
box_pub_upgrade --skip ap_recaptcha --skip some_pkg
```

**参数说明**

- `--yes`：跳过确认，直接执行升级
- `--no-commit`：只更新依赖与 lock，不执行 git commit/push
- `--private-host`：私服 hosted url 关键字（可多次指定）。默认不过滤：任何 hosted/url 都算私有依赖
- `--skip`：跳过某些包名（可多次指定）

**示例**

- `box_pub_upgrade`：默认交互：比对 -> 展示清单 -> 确认升级
- `box_pub_upgrade --yes --no-commit`：直接升级（不提交）
- `box_pub_upgrade --private-host my.private.repo`：仅升级 url 含关键词的 hosted 私有依赖

**文档**

[README.md](src/box_tools/flutter/pub_upgrade/README.md)

---

<a id="flutter-pub_version"></a>

## flutter/pub_version

<a id="box_tools-flutter-pub_version-tool"></a>

### box_pub_version

**简介**：升级 Flutter pubspec.yaml 的 version（支持交互选择 minor/patch，可选 git 提交）

**命令**：`box_pub_version`

**用法**

```bash
box_pub_version
box_pub_version minor
box_pub_version patch --no-git
box_pub_version minor --file path/to/pubspec.yaml
```

**参数说明**

- `--file`：指定 pubspec.yaml 路径（默认 ./pubspec.yaml）
- `--no-git`：只改版本号，不执行 git add/commit/push

**示例**

- `box_pub_version`：进入交互菜单选择升级级别
- `box_pub_version patch --no-git`：仅更新补丁号，不提交

**文档**

[README.md](src/box_tools/flutter/pub_version/README.md)

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


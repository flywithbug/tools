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
  - [`box`](#box-box)
- [flutter](#flutter)
  - [`pub_publish`](#box_tools-flutter-pub_publish)
  - [`pub_upgrade`](#box_tools-flutter-pub_upgrade)
  - [`pub_version`](#box_tools-flutter-pub_version)
  - [`riverpod_gen`](#box_tools-flutter-riverpod_gen)
- [translate](#translate)
  - [`translate`](#box_tools-translate-ai_translate)
  - [`slang_i18n`](#box_tools-translate-slang_i18n)
  - [`strings_i18n`](#box_tools-translate-strings_i18n)

---

<a id="section"></a>

## 工具总览

### box（工具集管理）

- **[`box`](#box-box)**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表（[文档](src/box/box.md)）

### flutter

- **[`pub_publish`](#box_tools-flutter-pub_publish)**：自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，提交并发布（支持 release 分支规则）（[文档](src/box_tools/flutter/pub_publish.md)）
- **[`pub_upgrade`](#box_tools-flutter-pub_upgrade)**：升级 pubspec.yaml 中的私有 hosted/url 依赖（比对清单 + 确认；升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）（[文档](src/box_tools/flutter/pub_upgrade.md)）
- **[`pub_version`](#box_tools-flutter-pub_version)**：升级 pubspec.yaml 的 version（支持交互选择 minor/patch）（[文档](src/box_tools/flutter/pub_version.md)）
- **[`riverpod_gen`](#box_tools-flutter-riverpod_gen)**：生成 Riverpod StateNotifier + State 模板文件（notifier/state）（[文档](src/box_tools/flutter/riverpod_gen.md)）

### translate

- **[`translate`](#box_tools-translate-ai_translate)**：OpenAI 翻译/JSON 工具底座：平铺 JSON 翻译（key 不变、只翻 value、占位符守护）+ 环境自检（文档缺失：`src/box_tools/translate/ai_translate.md`）
- **[`slang_i18n`](#box_tools-translate-slang_i18n)**：Flutter slang i18n（flat .i18n.json）排序 / 冗余检查清理 / 增量翻译（支持交互）（[文档](src/box_tools/translate/slang_i18n.md)）
- **[`strings_i18n`](#box_tools-translate-strings_i18n)**：iOS/Xcode .strings 多语言：扫描/同步/排序/重复与冗余清理/增量翻译（支持交互）（[文档](src/box_tools/translate/strings_i18n.md)）

---

<a id="section"></a>

## 工具集文档索引

### box（工具集管理）

- **box**：[src/box/box.md](src/box/box.md)

### flutter

- **pub_publish**：[src/box_tools/flutter/pub_publish.md](src/box_tools/flutter/pub_publish.md)
- **pub_upgrade**：[src/box_tools/flutter/pub_upgrade.md](src/box_tools/flutter/pub_upgrade.md)
- **pub_version**：[src/box_tools/flutter/pub_version.md](src/box_tools/flutter/pub_version.md)
- **riverpod_gen**：[src/box_tools/flutter/riverpod_gen.md](src/box_tools/flutter/riverpod_gen.md)

### translate

- **translate**：未找到文档 `src/box_tools/translate/ai_translate.md`（请创建该文件或在 BOX_TOOL['docs'] 指定）
- **slang_i18n**：[src/box_tools/translate/slang_i18n.md](src/box_tools/translate/slang_i18n.md)
- **strings_i18n**：[src/box_tools/translate/strings_i18n.md](src/box_tools/translate/strings_i18n.md)

---

<a id="box-box"></a>

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
- `tools --full`：显示工具的详细信息（options/examples）

**示例**

- `box doctor`：诊断环境（python/pipx/PATH）
- `box update`：更新工具集
- `box tools`：列出当前工具与简介

**文档**

[src/box/box.md](src/box/box.md)

---

<a id="flutter"></a>

## flutter

<a id="box_tools-flutter-pub_publish"></a>

### pub_publish

**简介**：自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，提交并发布（支持 release 分支规则）

**命令**：`pub_publish`

**用法**

```bash
pub_publish --msg fix crash on iOS
pub_publish --msg feat add new api --no-publish
pub_publish --pubspec path/to/pubspec.yaml --changelog path/to/CHANGELOG.md --msg release notes
pub_publish --msg hotfix --dry-run
```

**参数说明**

- `--pubspec`：pubspec.yaml 路径（默认 ./pubspec.yaml）
- `--changelog`：CHANGELOG.md 路径（默认 ./CHANGELOG.md）
- `--msg`：更新说明（必填；可写多段，不需要引号）
- `--no-pull`：跳过 git pull
- `--no-git`：跳过 git add/commit/push
- `--no-publish`：跳过 flutter pub publish
- `--skip-pub-get`：跳过 flutter pub get
- `--dry-run`：仅打印将执行的操作，不改文件、不跑命令

**示例**

- `pub_publish --msg fix null error`：拉代码→升级版本→更新 changelog→pub get→提交→发布
- `pub_publish --msg release notes --no-publish`：只提交不发布
- `pub_publish --msg try --dry-run`：预演一次，不做任何修改

**文档**

[src/box_tools/flutter/pub_publish.md](src/box_tools/flutter/pub_publish.md)

---

<a id="box_tools-flutter-pub_upgrade"></a>

### pub_upgrade

**简介**：升级 pubspec.yaml 中的私有 hosted/url 依赖（比对清单 + 确认；升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）

**命令**：`pub_upgrade`

**用法**

```bash
pub_upgrade
pub_upgrade --yes
pub_upgrade --no-commit
pub_upgrade --private-host dart.cloudsmith.io
pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo
pub_upgrade --skip ap_recaptcha --skip some_pkg
```

**参数说明**

- `--yes`：跳过确认，直接执行升级
- `--no-commit`：只更新依赖与 lock，不执行 git commit/push
- `--private-host`：私服 hosted url 关键字（可多次指定）。默认不过滤：任何 hosted/url 都算私有依赖
- `--skip`：跳过某些包名（可多次指定）

**示例**

- `pub_upgrade`：默认交互：比对 -> 展示清单 -> 确认升级
- `pub_upgrade --yes --no-commit`：直接升级（不提交）
- `pub_upgrade --private-host my.private.repo`：仅升级 url 含关键词的 hosted 私有依赖

**文档**

[src/box_tools/flutter/pub_upgrade.md](src/box_tools/flutter/pub_upgrade.md)

---

<a id="box_tools-flutter-pub_version"></a>

### pub_version

**简介**：升级 pubspec.yaml 的 version（支持交互选择 minor/patch）

**命令**：`pub_version`

**用法**

```bash
pub_version
pub_version minor
pub_version patch --no-git
pub_version minor --file path/to/pubspec.yaml
```

**参数说明**

- `--file`：指定 pubspec.yaml 路径（默认 ./pubspec.yaml）
- `--no-git`：只改版本号，不执行 git add/commit/push

**示例**

- `pub_version`：进入交互菜单选择升级级别
- `pub_version patch --no-git`：仅更新补丁号，不提交

**文档**

[src/box_tools/flutter/pub_version.md](src/box_tools/flutter/pub_version.md)

---

<a id="box_tools-flutter-riverpod_gen"></a>

### riverpod_gen

**简介**：生成 Riverpod StateNotifier + State 模板文件（notifier/state）

**命令**：`riverpod_gen`

**用法**

```bash
riverpod_gen
riverpod_gen Product
riverpod_gen product_item --out lib/features/product
riverpod_gen Product --force
riverpod_gen Product --no-copywith
riverpod_gen Product --legacy
```

**参数说明**

- `--out`：输出目录（默认当前目录）
- `--force`：覆盖已存在文件
- `--no-copywith`：不生成 copy_with_extension 注解与 part '*.g.dart'
- `--legacy`：notifier 使用 flutter_riverpod/legacy.dart（默认启用 legacy）
- `--modern`：notifier 使用 flutter_riverpod/flutter_riverpod.dart

**示例**

- `riverpod_gen`：交互输入类名与输出目录
- `riverpod_gen Product`：在当前目录生成 product_notifier.dart 与 product_state.c.dart
- `riverpod_gen product_item --out lib/features/product`：在指定目录生成 product_item_* 文件
- `riverpod_gen Product --force`：覆盖已存在文件

**文档**

[src/box_tools/flutter/riverpod_gen.md](src/box_tools/flutter/riverpod_gen.md)

---

<a id="translate"></a>

## translate

<a id="box_tools-translate-ai_translate"></a>

### translate

**简介**：OpenAI 翻译/JSON 工具底座：平铺 JSON 翻译（key 不变、只翻 value、占位符守护）+ 环境自检

**命令**：`translate`

**用法**

```bash
translate
translate --help
translate doctor
translate translate --src-lang en --tgt-locale zh_Hant --in input.json --out output.json
```

**参数说明**

- `doctor`：检查 OpenAI SDK / OPENAI_API_KEY 环境变量 / Python 环境
- `translate`：翻译平铺 JSON（key 不变，只翻 value），输出为 JSON
- `--model`：选择模型（默认 gpt-4o）
- `--api-key`：显式传入 API key（优先于环境变量）

**示例**

- `translate`：显示简介 + 检查 OPENAI_API_KEY 是否已配置
- `translate translate --src-lang en --tgt-locale zh_Hant --in i18n/en.json --out i18n/zh_Hant.json`：翻译一个平铺 JSON 文件

**文档**

- 未找到文档：`src/box_tools/translate/ai_translate.md`（请创建该文件）

---

<a id="box_tools-translate-slang_i18n"></a>

### slang_i18n

**简介**：Flutter slang i18n（flat .i18n.json）排序 / 冗余检查清理 / 增量翻译（支持交互）

**命令**：`slang_i18n`

**用法**

```bash
slang_i18n
slang_i18n init
slang_i18n doctor
slang_i18n sort
slang_i18n check
slang_i18n clean --yes
slang_i18n translate --api-key $OPENAI_API_KEY
```

**参数说明**

- `--api-key`：OpenAI API key（也可用环境变量 OPENAI_API_KEY）
- `--model`：模型（默认 gpt-4o，且可覆盖配置 openAIModel）
- `--full`：全量翻译（默认增量翻译）
- `--yes`：clean 删除冗余时跳过确认
- `--no-exitcode-3`：check 发现冗余时仍返回 0（默认返回 3）

**示例**

- `slang_i18n init`：生成 slang_i18n.yaml 模板（新 schema：source/target 都含 code+name_en）
- `slang_i18n translate --api-key $OPENAI_API_KEY`：增量翻译缺失的 keys
- `slang_i18n clean --yes`：删除所有冗余 key（不询问）

**文档**

[src/box_tools/translate/slang_i18n.md](src/box_tools/translate/slang_i18n.md)

---

<a id="box_tools-translate-strings_i18n"></a>

### strings_i18n

**简介**：iOS/Xcode .strings 多语言：扫描/同步/排序/重复与冗余清理/增量翻译（支持交互）

**命令**：`strings_i18n`

**用法**

```bash
strings_i18n
strings_i18n options
strings_i18n init
strings_i18n doctor
strings_i18n scan
strings_i18n sync
strings_i18n sort
strings_i18n dupcheck
strings_i18n dedupe --yes --keep first
strings_i18n check
strings_i18n clean --yes
strings_i18n translate-core --api-key $OPENAI_API_KEY
strings_i18n translate-target --api-key $OPENAI_API_KEY
strings_i18n gen-l10n
```

**参数说明**

- `--config`：配置文件路径（默认 strings_i18n.yaml）
- `--languages`：languages.json 路径（默认 languages.json）
- `--api-key`：OpenAI API key（也可用环境变量 OPENAI_API_KEY）
- `--model`：模型（命令行优先；不传则用配置 openAIModel；默认 gpt-4o）
- `--full`：全量翻译（默认增量：只补缺失/空值 key）
- `--yes`：clean/dedupe 删除时跳过确认
- `--keep`：dedupe 保留策略：first/last（默认 first）
- `--no-exitcode-3`：check/dupcheck 发现问题时仍返回 0（默认返回 3）
- `--dry-run`：预览模式（不写入文件）

**文档**

[src/box_tools/translate/strings_i18n.md](src/box_tools/translate/strings_i18n.md)

---


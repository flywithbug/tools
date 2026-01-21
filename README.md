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
- [flutter/pub_publish](#flutter-pub_publish)
  - [`box_pub_publish`](#box_tools-flutter-pub_publish-tool)
- [flutter/pub_upgrade](#flutter-pub_upgrade)
  - [`box_pub_upgrade`](#box_tools-flutter-pub_upgrade-tool)
- [flutter/pub_version](#flutter-pub_version)
  - [`box_pub_version`](#box_tools-flutter-pub_version-tool)

---

<a id="section"></a>

## 工具总览

### box（工具集管理）

- **[`box`](#box-tool)**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表（[README.md](src/box/README.md)）

### flutter/pub_publish

- **[`box_pub_publish`](#box_tools-flutter-pub_publish-tool)**：自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，发布前检查（可交互处理 warning），提交并发布（支持 release 分支规则）（文档缺失：`src/box_tools/flutter/pub_publish/README.md`）

### flutter/pub_upgrade

- **[`box_pub_upgrade`](#box_tools-flutter-pub_upgrade-tool)**：升级 pubspec.yaml 中的私有 hosted/url 依赖（比对清单 + 确认；升级不跨 next minor，例如 3.45.* 只能升级到 < 3.46.0）（[README.md](src/box_tools/flutter/pub_upgrade/README.md)）

### flutter/pub_version

- **[`box_pub_version`](#box_tools-flutter-pub_version-tool)**：升级 Flutter pubspec.yaml 的 version（支持交互选择 minor/patch，可选 git 提交）（[README.md](src/box_tools/flutter/pub_version/README.md)）

---

<a id="section"></a>

## 工具集文档索引

### box（工具集管理）

- **box**：[README.md](src/box/README.md)

### flutter/pub_publish

- **box_pub_publish**：未找到文档 `src/box_tools/flutter/pub_publish/README.md`（请创建该文件或在 BOX_TOOL['docs'] 指定）

### flutter/pub_upgrade

- **box_pub_upgrade**：[README.md](src/box_tools/flutter/pub_upgrade/README.md)

### flutter/pub_version

- **box_pub_version**：[README.md](src/box_tools/flutter/pub_version/README.md)

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

<a id="flutter-pub_publish"></a>

## flutter/pub_publish

<a id="box_tools-flutter-pub_publish-tool"></a>

### box_pub_publish

**简介**：自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，发布前检查（可交互处理 warning），提交并发布（支持 release 分支规则）

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
- `--yes-warnings`：发布检查出现 warning 时仍继续提交并发布（非交互/CI 推荐）
- `--dry-run`：仅打印将执行的操作，不改文件、不跑命令

**示例**

- `box_pub_publish --msg fix null error`：拉代码→升级版本→更新 changelog→pub get→检查(可交互)→提交→发布
- `box_pub_publish --msg release notes --no-publish`：只提交不发布
- `box_pub_publish --msg release notes --yes-warnings`：检查有 warning 也自动继续提交并发布（适合 CI）
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
box_pub_upgrade --no-git
box_pub_upgrade --private-host dart.cloudsmith.io
box_pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo
box_pub_upgrade --skip ap_recaptcha --skip some_pkg
```

**参数说明**

- `--yes`：跳过确认，直接执行升级
- `--no-git`：只更新依赖与 lock，不执行 git pull/commit/push（兼容 --no-commit）
- `--private-host`：私服 hosted url 关键字（可多次指定）。默认不过滤：任何 hosted/url 都算私有依赖
- `--skip`：跳过某些包名（可多次指定）

**示例**

- `box_pub_upgrade`：默认交互：比对 -> 展示清单 -> 确认升级
- `box_pub_upgrade --yes --no-git`：直接升级（不提交/不拉取）
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


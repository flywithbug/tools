# tools

一个用于日常开发/发布的小工具集（Python 脚本工具库）。

## 安装

使用 pipx 从 GitHub 安装/强制覆盖安装：

```bash
pipx install --force "git+https://github.com/flywithbug/tools.git"
```


## 工具集介绍

## 工具总览

### box（工具集管理）

- **`box`**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表

### flutter

- **`pub_publish`**：自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，提交并发布（支持 release 分支规则）
- **`pub_upgrade`**：升级 pubspec.yaml 中的私服 hosted/url 依赖（比对清单 + 确认；release 分支可选跟随 x.y.*）
- **`pub_version`**：升级 pubspec.yaml 的 version（支持交互选择 minor/patch）

---

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

## flutter

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

### pub_upgrade

**简介**：升级 pubspec.yaml 中的私服 hosted/url 依赖（比对清单 + 确认；release 分支可选跟随 x.y.*）

**命令**：`pub_upgrade`

**用法**

```bash
pub_upgrade
pub_upgrade --yes
pub_upgrade --no-commit
pub_upgrade --follow-release
pub_upgrade --no-follow-release
pub_upgrade --private-host dart.cloudsmith.io
pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo
```

**参数说明**

- `--yes`：跳过确认，直接执行升级
- `--no-commit`：只更新依赖与 lock，不执行 git commit/push
- `--follow-release`：在 release-x.y 分支：仅升级到 x.y.*（并允许从更低版本升上来）
- `--no-follow-release`：在 release-x.y 分支：不跟随 x.y.*，走“非 release 分支策略”
- `--private-host`：私服 hosted url 关键字（可多次指定）。默认 dart.cloudsmith.io
- `--skip`：跳过某些包名（可多次指定）

**示例**

- `pub_upgrade`：默认交互：比对 -> 展示清单 -> 确认升级
- `pub_upgrade --yes --no-commit`：直接升级（不提交）
- `pub_upgrade --follow-release`：release 分支严格跟随 x.y.*

**文档**

[src/box_tools/flutter/pub_upgrade.md](src/box_tools/flutter/pub_upgrade.md)

---

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


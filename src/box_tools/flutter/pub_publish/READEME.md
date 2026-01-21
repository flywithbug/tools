# box_pub_publish

`box_pub_publish` 用于把 Flutter 包发布这件麻烦事，变成一条可重复、可审计的命令：

- 自动升级 `pubspec.yaml` 的 `version:`（默认 patch +1，保留 `+build`）
- 自动在 `CHANGELOG.md` 头部插入新版本区块
- 执行 `flutter pub get`
- （可选）发布前检查：`flutter analyze` + `git` 变更白名单
    - 有 `error`：直接中止
    - 只有 `info/warning`：可交互选择继续，或用 `--yes-warnings` 在 CI 中自动继续
- （可选）`git add .` → `git commit` → `git push`
- （可选）`flutter pub publish --force`
- 支持 `release-<x.y.z>` 分支版本对齐规则

> 工具集命名规则：命令统一加 `box_` 前缀。

---

## 安装与查看帮助

随工具集一起安装后：

```bash
box_pub_publish --help
```

---

## 快速开始

最常用：写一段发布说明（不需要引号，可多段，会自动拼接）：

```bash
box_pub_publish --msg fix crash on iOS
```

只提交不发布（会跑检查，默认仍会 `flutter pub get`）：

```bash
box_pub_publish --msg release notes --no-publish
```

检查发现 issue（info/warning）也继续（适合 CI）：

```bash
box_pub_publish --msg release notes --yes-warnings
```

预演：不改文件、不执行外部命令：

```bash
box_pub_publish --msg hotfix --dry-run
```

---

## 参数说明

- `--pubspec`：`pubspec.yaml` 路径（默认 `./pubspec.yaml`）
- `--changelog`：`CHANGELOG.md` 路径（默认 `./CHANGELOG.md`）
- `--msg`：更新说明（必填，可多段）

### Git 相关
- `--no-pull`：跳过 `git pull`
- `--no-git`：跳过 `git add/commit/push`
    - 如果当前目录不是 git 仓库或未安装 git，工具也会自动降级为 no-git（并提示）

### Flutter 相关
- `--skip-pub-get`：跳过 `flutter pub get`
- `--no-publish`：跳过 `flutter pub publish --force`

### 检查与交互
- `--skip-checks`：跳过发布前检查（`flutter analyze` + `git` 变更白名单）
- `--yes-warnings`：检查出现 `info/warning` 时仍继续提交并发布（非交互/CI 推荐）
- `--dry-run`：预演模式（不改文件、不执行外部命令）

---

## 发布前检查（Pre-publish Checks）

当你没有传 `--skip-checks`，并且实际要发布（未传 `--no-publish`）时，会执行：

1) **Git 变更白名单检查**（仅在 git 仓库中执行）
2) **`flutter analyze`**

### 1) Git 变更白名单（非常重要）

本工具的提交逻辑是 **`git add .`**。为了避免不小心提交“脏文件”，发布前检查会强约束：  
工作区变更只能出现在以下路径：

- `pubspec.yaml`
- `CHANGELOG.md`
- 当前包目录内的任意 `pubspec.lock`（例如 `pubspec.lock` 或 `example/pubspec.lock`）

如果发现其它文件变更，会直接失败并列出文件清单，让你先清理/提交/暂存后再继续。

### 2) flutter analyze 的处理策略

- 若发现 `error • ...`：**直接中止**（不 commit、不 publish）
- 若只有 `info • ...` / `warning • ...`：
    - 交互环境：会询问你是否继续提交+发布
    - 非交互环境（CI）：必须传 `--yes-warnings` 才会继续，否则中止

---

## 版本升级规则

### 普通分支

默认：`patch + 1`（保留 build）

例：

- `1.2.3` → `1.2.4`
- `3.45.1+2026012100` → `3.45.2+2026012100`

### release 分支

当当前分支名为 `release-<x.y.z>`（例如 `release-3.45.5`）时：

- 若当前版本 `< 分支版本`：直接提升到分支版本（保留 build）
- 若当前版本 `>= 分支版本`：`patch + 1`（保留 build）

例：

- 分支 `release-3.45.5`，当前 `3.45.1+2026` → `3.45.5+2026`
- 分支 `release-3.45.5`，当前 `3.45.5+2026` → `3.45.6+2026`

> 版本比较只看 `x.y.z`，忽略 `+build`。

---

## CHANGELOG.md 写入格式

会在文件头插入：

```text
## <new_version>

- <yyyy-mm-dd HH:MM>
- <msg>
```

如果 `CHANGELOG.md` 不存在，会自动创建。

---

## Git 提交信息

commit message 形如：

```text
build: <project_name> + <new_version>
```

其中 `<project_name>` 来自 `pubspec.yaml` 的 `name:` 字段。

---

## 返回码（Exit Codes）

| 返回码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 执行失败（命令失败 / 解析失败 / 检查失败等） |
| 2 | 参数错误（如 msg 为空 / pubspec 不存在） |
| 130 | Ctrl+C 取消 |

---

## 建议的使用习惯

- 先跑一次预演：`--dry-run`（确认动作链）
- 在 CI 中固定加：`--yes-warnings`（避免卡在交互）
- 如果你就是要“先更新版本和 changelog，但暂时不发布”：用 `--no-publish`

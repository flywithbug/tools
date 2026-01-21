# box_pub_publish

`box_pub_publish` 用于 Flutter 包发布流程自动化：

- 自动升级 `pubspec.yaml` 的 `version:`（默认 patch +1，保留 `+build`）
- 自动更新 `CHANGELOG.md`（在文件头插入新版本区块）
- 执行 `flutter pub get`
- 可选执行 `git add/commit/push`
- 可选执行 `flutter pub publish --force`
- 支持 `release-<x.y.z>` 分支规则

> 默认命令名遵循工具集规则：统一加 `box_` 前缀。

---

## 安装

随工具集一起安装：

```bash
box_pub_publish --help
```

---

## 快速开始

最常用：写一段发布说明（不需要引号，可多段，会自动拼接）：

```bash
box_pub_publish --msg fix crash on iOS
```

只提交不发布：

```bash
box_pub_publish --msg release notes --no-publish
```

预演：不改文件、不执行外部命令（会打印将执行的动作）：

```bash
box_pub_publish --msg hotfix --dry-run
```

---

## 参数

- `--pubspec`：pubspec.yaml 路径（默认 `./pubspec.yaml`）
- `--changelog`：CHANGELOG.md 路径（默认 `./CHANGELOG.md`）
- `--msg`：更新说明（必填，可多段）
- `--no-pull`：跳过 `git pull`
- `--no-git`：跳过 `git add/commit/push`（如果当前目录不是 git 仓库，也会自动降级为 no-git）
- `--skip-pub-get`：跳过 `flutter pub get`
- `--no-publish`：跳过 `flutter pub publish --force`
- `--dry-run`：预演模式

---

## 版本升级规则

### 普通分支

默认：`patch + 1`（保留 build）

例：

- `1.2.3` → `1.2.4`
- `3.45.1+2026012100` → `3.45.2+2026012100`

### release 分支

当当前分支名为：`release-<x.y.z>`（例如 `release-3.45.5`）时：

- 若当前版本 `< 分支版本`：直接提升到分支版本（保留 build）
- 若当前版本 `>= 分支版本`：`patch + 1`（保留 build）

例：

- 分支 `release-3.45.5`，当前 `3.45.1+2026` → `3.45.5+2026`
- 分支 `release-3.45.5`，当前 `3.45.5+2026` → `3.45.6+2026`

> 版本比较只看 `x.y.z`，忽略 `+build`。

---

## CHANGELOG.md 格式

会在文件头插入一段：

```text
## <new_version>

- <yyyy-mm-dd HH:MM>
- <msg>
```

如果 `CHANGELOG.md` 不存在，会自动创建。

---

## Git 提交信息

commit message 格式：

```text
build: <project_name> + <new_version>
```

会自动 `git add`：

- pubspec.yaml
- CHANGELOG.md
- pubspec.lock（如果存在）

---

## 返回码（Exit Codes）

| 返回码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 执行失败（命令失败 / 解析失败等） |
| 2 | 参数错误（如 msg 为空 / pubspec 不存在） |
| 130 | Ctrl+C 取消 |

---

## 注意事项

- 建议在 Flutter 包根目录运行（存在 `pubspec.yaml`）。
- `--dry-run` 不会修改任何文件，不会执行任何外部命令。
- 若你在非 git 仓库运行且未传 `--no-git`，工具会自动提示并跳过 git 操作。

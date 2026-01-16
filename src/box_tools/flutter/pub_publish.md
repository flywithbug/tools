````markdown
# pub_publish

`pub_publish` 用于 **自动升级 Flutter 包版本号**、**更新 CHANGELOG**、执行 `flutter pub get`，并可选执行 `git add / commit / push` 与 `flutter pub publish`。

它适合：

- 日常发包（单人手动）
- 团队统一的发布流程（可脚本化）
- release 分支规范化版本号（`release-x.y.z`）

---

## 安装

`pub_publish` 随工具集一起安装。

安装完成后可运行：

```sh
pub_publish --help
```

---

## 基本用法

必须提供 `--msg`（更新说明，不需要引号，可多段）：

```sh
pub_publish --msg fix crash on iOS
```

默认行为（全自动链路）：

1. `git pull`
2. 更新 `pubspec.yaml` 的 `version:`
3. 在 `CHANGELOG.md` 文件头插入新版本记录
4. `flutter pub get`
5. `git add / commit / push`
6. `flutter pub publish --force`

---

## 版本升级规则

### 1) 非 release 分支

- 永远 **patch + 1**
- 如果版本号包含 `+build`，`build` 会被原样保留

示例：

- `1.2.3` → `1.2.4`
- `3.44.1+2026011600` → `3.44.2+2026011600`

### 2) release 分支（`release-x.y.z`）

当当前分支名匹配 `release-<x.y.z>`：

- 如果当前版本 **小于** 分支版本：直接提升到分支版本（保留 build）
- 其他情况：**patch + 1**（保留 build）

---

## 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--pubspec` | `pubspec.yaml` | pubspec.yaml 路径 |
| `--changelog` | `CHANGELOG.md` | CHANGELOG.md 路径 |
| `--msg ...` | 无 | 更新说明（必填，可多段） |
| `--no-pull` | 否 | 跳过 `git pull` |
| `--no-git` | 否 | 跳过 `git add/commit/push` |
| `--skip-pub-get` | 否 | 跳过 `flutter pub get` |
| `--no-publish` | 否 | 跳过 `flutter pub publish` |
| `--dry-run` | 否 | 预演：不改文件、不执行命令 |

---

## 常用示例

### 预演一次（不改文件、不跑命令）

```sh
pub_publish --msg test publish flow --dry-run
```

### 只提交不发布

```sh
pub_publish --msg release notes --no-publish
```

### 只改版本 + changelog（不碰 git）

```sh
pub_publish --msg bump version only --no-git --no-publish
```

---

## CHANGELOG 格式

脚本会在 `CHANGELOG.md` 文件头插入：

```text
## <new_version>

- YYYY-MM-DD HH:MM
- <your message>
```

---

## 注意事项

- `flutter pub publish --force` 会跳过交互确认；请确保你的包已经满足发布条件。
- 若 `pubspec.lock` 存在，会一并 `git add`；不存在则自动跳过。

````

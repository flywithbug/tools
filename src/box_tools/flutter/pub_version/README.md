# box_pub_version

`box_pub_version` 用于升级 Flutter 项目 `pubspec.yaml` 中的 `version:` 字段，并可选自动执行 `git add / commit / push`。

它支持**交互式选择升级级别**，也支持**非交互模式**（适合脚本或 CI）。

---

## 安装

`box_pub_version` 随工具集一起安装。

安装完成后，命令应当可以直接使用：

```sh
box_pub_version --help
```

---

## 使用方式

默认情况下，`box_pub_version` 会读取**当前目录**下的 `pubspec.yaml`：

```sh
box_pub_version
```

---

## 交互模式（默认）

不带参数执行会进入交互菜单：

```text
📦 当前版本: 3.44.1+2026011600
请选择升级级别：
1 - 次版本号（minor）升级 → 3.45.0+2026011600
2 - 补丁号（patch）升级 → 3.44.2+2026011600
0 - 退出
请输入 0 / 1 / 2（或 q 退出）:
```

说明：

- `1`：次版本号（minor）升级  
- `2`：补丁号（patch）升级  
- `0 / q`：退出，不做任何修改  

---

## 升级规则

以版本号 `X.Y.Z+BUILD` 为例：

### minor（次版本升级）

```text
X.(Y+1).0+BUILD
```

示例：

```text
1.0.0         → 1.1.0
3.44.1+2026   → 3.45.0+2026
```

### patch（补丁升级）

```text
X.Y.(Z+1)+BUILD
```

示例：

```text
1.0.0         → 1.0.1
3.44.1+2026   → 3.44.2+2026
```

> `+BUILD`（如 `+2026011600`）会被**完整保留**，不会自动变更。

---

## 非交互模式（适合脚本 / CI）

可以直接指定升级级别，跳过交互：

```sh
box_pub_version minor
box_pub_version patch
```

---

## 指定 pubspec.yaml 路径

当你不在 Flutter 项目根目录时，可以手动指定文件路径：

```sh
box_pub_version minor --file path/to/pubspec.yaml
```

---

## 跳过 Git 操作

默认情况下，`box_pub_version` 会尝试执行以下 Git 操作：

- `git add pubspec.yaml`
- `git commit -m "chore(pub): bump version to <new_version>"`
- `git push`

如果你只想修改版本号，不希望触发 git 行为：

```sh
box_pub_version patch --no-git
```

另外，如果当前目录不是 git 仓库，工具会**自动跳过 git 操作**（效果等同 `--no-git`）。

---

## 返回码（Exit Codes）

| 返回码 | 含义 |
| --- | --- |
| 0 | 成功，或用户主动退出，或已自动跳过 git |
| 1 | 版本已更新，但 git 操作失败 |
| 2 | 参数错误、文件不存在或解析失败 |
| 130 | 用户 Ctrl+C 取消 |

---

## 常见问题

### 找不到 pubspec.yaml

默认读取 `./pubspec.yaml`。请确认：

- 当前目录是 Flutter 项目根目录
- 或使用 `--file` 明确指定路径

```sh
box_pub_version minor --file /absolute/or/relative/path/to/pubspec.yaml
```

### git 操作失败怎么办？

即使 git 失败，**版本号已经写入文件**。

你可以：

- 检查当前目录是否是 git 仓库
- 检查远端权限或网络
- 使用 `--no-git` 跳过 git 行为

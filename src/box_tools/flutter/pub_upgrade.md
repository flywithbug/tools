````markdown
# pub_upgrade

`pub_upgrade` 是一个 Flutter 私服依赖升级工具：  
它会读取项目 `pubspec.yaml` 中的 **hosted/url 私服依赖**，通过 `flutter pub outdated --json` 比对可升级项，先输出“升级清单（从哪个版本到哪个版本）”，再询问你是否执行升级。

它还内置了两条安全策略：

- **release 分支（release-x.y）**：可选择是否“跟随当前 release 次版本号（x.y.*）”升级
- **非 release 分支**：只升级**小版本**（不升级**大版本**）

> 说明：`pub_upgrade` 只检查 `pubspec.yaml` 里声明为 hosted/url 的依赖，不会通过 `ap_ / at_` 前缀判断。

---

## 安装

`pub_upgrade` 随工具集一起安装（仓库：`flywithbug/tools`）。安装完成后可直接运行：

```sh
pub_upgrade --help
````

---

## 使用前提

* 在 Flutter 项目根目录运行（同级存在 `pubspec.yaml`）
* 可用的 `flutter` 命令在 `PATH` 中
* 项目使用 Git（默认会自动提交/推送，可用 `--no-commit` 跳过）

---

## 快速开始

进入交互流程（推荐）：

```sh
pub_upgrade
```

典型流程：

1. 先执行 `flutter pub get`（保证本地依赖状态一致）
2. 运行 `flutter pub outdated --json` 生成升级计划
3. 输出清单：`包名: 当前版本 -> 最新版本`
4. 询问是否升级
5. 执行升级后再次 `flutter pub get`
6. 默认执行 `git add/commit/push`（可跳过）

---

## 私服依赖识别规则

`pub_upgrade` 只处理 `pubspec.yaml` 中类似以下形式的依赖块（hosted + url）：

```yaml
dependencies:
  at_i18n:
    hosted:
      url: https://dart.cloudsmith.io/apex-dao-llc/app/
      name: at_i18n
    version: ^0.0.3
```

仅当 hosted url 命中“私服关键字”时才会被纳入升级（默认关键字为 `dart.cloudsmith.io`）。

---

## 分支策略

### 1) release 分支（release-x.y）

当当前分支名匹配 `release-x.y` 时，`pub_upgrade` 会询问：

* 是否跟随 `x.y.*` 升级？

#### 跟随（follow-release = yes）

只允许升级到 `x.y.*` 范围内的版本，例如 `release-3.41`：

* ✅ 允许：`3.41.0 -> 3.41.8`
* ✅ 允许：`3.40.9 -> 3.41.2`（从更低版本升到 3.41.*）
* ❌ 不允许：`3.41.2 -> 3.42.0`

你也可以用参数直接指定：

```sh
pub_upgrade --follow-release
```

#### 不跟随（follow-release = no）

会改用“非 release 分支策略”（不升级大版本）：

```sh
pub_upgrade --no-follow-release
```

---

### 2) 非 release 分支

默认策略：**只升级小版本，不升级大版本**。

* ✅ 允许：`1.2.3 -> 1.8.0`
* ✅ 允许：`0.0.3 -> 0.1.0`
* ❌ 不允许：`1.9.0 -> 2.0.0`

---

## 常用命令

### 交互升级（默认）

```sh
pub_upgrade
```

### 跳过确认，直接升级

```sh
pub_upgrade --yes
```

### 升级但不提交/推送（只修改文件）

```sh
pub_upgrade --no-commit
```

### 指定私服域名关键字（可多次指定）

默认仅匹配 `dart.cloudsmith.io`。如果你的私服域名不同或有多个：

```sh
pub_upgrade --private-host dart.cloudsmith.io --private-host my.private.repo
```

### 跳过某些包

```sh
pub_upgrade --skip at_i18n --skip at_xxx
```

---

## 输出示例

比对完成后会输出升级清单：

```text
发现以下可升级依赖：
  - at_i18n: ^0.0.3 -> 0.0.5
  - at_theme: ^1.2.0 -> 1.3.1

是否执行升级？(y/N):
```

确认执行后会更新：

* `pubspec.yaml`
* `pubspec.lock`（通过 `flutter pub get` 刷新）

默认会进行 git 操作（除非 `--no-commit`）：

* `git add pubspec.yaml pubspec.lock`
* `git commit -m "up deps" + 变更摘要`
* `git push`（若存在远程分支）

---

## 返回码（Exit Codes）

| 返回码 | 含义                                |
| --- | --------------------------------- |
| 0   | 成功 / 无可升级项 / 用户取消                 |
| 1   | 执行失败（如 pub get/outdated/git 操作失败） |

---

## 常见问题

### 1) 为什么没有检测到任何可升级项？

可能原因：

* `pubspec.yaml` 中没有 hosted/url 依赖
* hosted url 未命中 `--private-host` 关键字（默认只匹配 `dart.cloudsmith.io`）
* `flutter pub outdated --json` 未返回该依赖的可升级版本

### 2) 为什么有些依赖没有被升级？

可能原因：

* 非 release 分支：被“大版本升级限制”过滤（例如 1.x -> 2.x）
* release 跟随模式：被 `x.y.*` 范围限制过滤

---

## License

MIT

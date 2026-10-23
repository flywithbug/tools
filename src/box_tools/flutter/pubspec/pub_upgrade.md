下面是**完整的 pub_upgrade PRD**（按你最新补充的三条关键规则固化：
1）只升级私有 hosted/url；2）不跨 next minor（< upper）；3）**优先 latest.version**；并且允许从低 minor 追到项目 minor 内）。

---

# PRD：pubspec / pub_upgrade（依赖升级模块）

## 1. 背景与目标

Flutter 项目里存在大量私有依赖（hosted + url），依赖版本需要在可控范围内升级，避免跨 minor 引入不兼容变更。人工比对 `flutter pub outdated --json` 与 `pubspec.yaml` 既费时又容易出错。

### 目标

提供 `pub_upgrade` 模块，实现：

* 自动识别 `pubspec.yaml` 中的**私有 hosted/url 依赖**
* 调用 `flutter pub outdated --json` 获取最新可升级版本
* 基于“项目版本锚定”的策略生成升级计划（plan）
* 交互确认后，**仅修改必要版本字段**（不破坏注释与结构）
* 可选执行 `flutter pub get` 更新 lock

---

## 2. 范围与非目标

### 2.1 本期范围

* 解析项目 `pubspec.yaml`
* 仅升级：

    * `dependencies`
    * `dev_dependencies`
    * （可选）`dependency_overrides`（默认可支持，但可以在实现中先只读不改；PRD允许实现支持）
* 依赖类型：**hosted 且包含 url** 的依赖块（私有 hosted/url）
* 升级来源：`flutter pub outdated --json`（允许 fallback 到 `dart pub outdated --json`）

### 2.2 非目标（明确不做）

* 不升级 pub.dev 普通依赖（无 hosted.url）
* 不新增/删除依赖
* 不调整依赖排序、缩进、换行、注释
* 不做 API 兼容性分析
* 不做自动 git pull/commit/push（除非未来作为可选增强）

---

## 3. 核心约束：保持 pubspec.yaml 文本结构不变

### 3.1 强约束

对 `pubspec.yaml` 的修改必须满足：

* **不改动任何注释**
* **不改动任何非目标行**
* **不重排任何键**
* **不改变缩进/换行风格**
* 只允许替换：

    * 单行版本依赖的版本 token
    * hosted 多行块中的 `version:` 行的版本 token

### 3.2 技术实现原则（约束转落地）

* **禁止** YAML parse + dump（会破坏注释/格式）
* 必须采用“文本扫描 + block 定位 + 精准替换”
* 写入方式：原子写入（`.tmp` 覆盖），**不生成 `.bak` 备份**

---

## 4. 输入与输出

### 4.1 输入

* `pubspec.yaml`（项目根目录）
* `flutter pub outdated --json` 输出（实时执行或用户提供 json 文件）

### 4.2 输出

* 终端输出升级计划（包含每个包的 current -> target）
* 若执行 apply：

    * 更新 `pubspec.yaml`（最小替换）
    * 可选执行 `flutter pub get`（更新 pubspec.lock）

---

## 5. 术语与判定规则

### 5.1 私有依赖定义（Private Hosted/URL Dependency）

一个依赖条目（block）满足以下条件即为“私有依赖”：

* 在其依赖块中同时存在：

    * `hosted:`
    * `url: <...>`

支持可选过滤条件 `private_host_keywords`：

* 若设置了关键字列表，则 url 必须包含任一关键字才视为私有
* 若未设置关键字列表，则任何 hosted+url 块都算私有

### 5.2 项目版本（App Version）

从 `pubspec.yaml` 顶层 `version:` 读取，支持：

* `3.45.0`
* `3.45.0+2026012103`
* 允许 `-pre`、`+build` 之类语法存在（用于合法性判断），但做比较时忽略 `-` 与 `+` 后的元信息

### 5.3 上限 upper bound（不跨 next minor）

若能解析出项目版本 `X.Y.*`：

* 计算 `upper = X.(Y+1).0`
* 所有依赖升级目标必须满足：`target < upper`（严格小于，exclusive）

说明：

* **允许依赖从低版本跨 minor 升级到项目 minor 内**

    * 例如项目 `3.45.*`，依赖 `3.42.0` 若存在 `3.45.1`，允许升级到 `3.45.1`

### 5.4 outdated 版本字段（来源）

对每个包读取以下字段（可能为空）：

* `current.version`
* `upgradable.version`
* `resolvable.version`
* `latest.version`

---

## 6. Target 选择策略（你最新要求：优先 latest.version）

这是 PRD 的核心决策。

### 6.1 当 upper 存在（能解析项目版本）

目标版本选择顺序：

1. 若 `latest.version` 存在且满足 `< upper` → **target = latest**
2. 否则若 `resolvable.version` 存在且 `< upper` → target = resolvable
3. 否则若 `upgradable.version` 存在且 `< upper` → target = upgradable
4. 否则 → 无 target（跳过）

> 强制“优先 latest”，而不是“在候选里挑最大”。

### 6.2 当 upper 不存在（无法解析项目版本）

退化策略：**不升级依赖 major**，并且仍优先 latest：

1. 若 `latest` 存在且 `major(latest) <= major(current)` → target = latest
2. 否则若 `resolvable` 存在且 `major(resolvable) <= major(current)` → target = resolvable
3. 否则若 `upgradable` 存在且 `major(upgradable) <= major(current)` → target = upgradable
4. 否则跳过

### 6.3 是否纳入升级计划

当且仅当：

* target 存在
* 且 `target > current`（语义比较，忽略 `^`、`+build`、`-pre`）

才把该包加入 plan。

---

## 7. 计划生成（Plan）

### 7.1 Plan 结构

每项至少包含：

* `name`：包名
* `current`：当前版本（来自 outdated current 或 pubspec）
* `target`：选定目标（按 6 的策略）
* `reason`（可选但推荐）：例如 `latest < upper`、`fallback to resolvable`、`upper missing (no major bump)` 等

### 7.2 Plan 生成流程

1. 读取 `pubspec.yaml` 为 lines（保留换行符）
2. 提取 `dependencies/dev_dependencies/(optional dependency_overrides)` 的每个依赖块
3. 在这些块中识别 private hosted/url 依赖集合 `private_deps`
4. 执行/读取 outdated json，构建 map
5. 遍历 outdated map：

    * 包必须在 pubspec 的 direct 依赖集合中（只处理 pubspec 中声明过的）
    * 包必须在 private_deps
    * 包不在 skip 列表（默认可带 `ap_recaptcha`）
    * 计算 upper（若能从 pubspec version 得到）
    * 按 6 的策略选 target
    * 若 target > current，加入 plan
6. plan 按包名排序输出

---

## 8. Apply：执行修改（最小替换）

### 8.1 依赖块识别（文本级）

在 section 内，一个依赖块定义为：

* 以 `␠␠<pkg>:` 开头（2 空格缩进）
* 一直到下一个同级 `␠␠<other>:` 或 section 结束

### 8.2 两种写法的替换规则

#### A. 单行版本

示例：

```yaml
ap_data: ^3.44.2
```

或：

```yaml
ap_data: 3.44.2
```

替换方式：

* 只替换版本 token（保留 `^` 是否存在、保留行尾空格/换行）
* 不触碰同一行的注释（若存在 `# ...`，必须保留）

#### B. hosted 多行块（私有依赖常见）

示例：

```yaml
ap_data:
  hosted:
    url: https://...
    name: ap_data
  version: 3.44.2
```

替换方式：

* 在块内定位 `version:` 行
* 仅替换其版本 token
* 其他行原样保留

### 8.3 写入

* 写入采用 atomic write（tmp -> replace）
* 不生成备份文件

### 8.4 执行后动作（可配置）

* 默认执行一次 `flutter pub get` 更新 lock
* 若后续加入 git 能力，则 summary 可用于 commit message（本期非目标）

---

## 9. CLI/交互行为（在 pubspec 工具内）

### 9.1 顶层入口

`pubspec upgrade` 进入子菜单

### 9.2 子菜单建议

* `scan`：只生成 plan 并打印，不修改文件
* `apply`：打印 plan + 二次确认 + 修改 pubspec + pub get

确认策略：

* `--yes`：跳过确认
* 否则交互确认

---

## 10. 异常与边界处理

* 找不到 `pubspec.yaml`：直接报错退出
* outdated json 执行失败：输出 stderr/stdout 关键内容并退出
* outdated json 解析失败：尝试从 stdout/stderr 提取 JSON（若未来需要；在 pubspec 版可先依赖 flutter 输出规范）
* pubspec 中找不到 version：upper 不存在 → 退化策略（不升 major）
* pubspec 中私有依赖块找不到 `version:` 且也不是单行版本：跳过并提示（不强行改结构）
* apply 后未发生任何变更：提示“没有实际修改”

---

## 11. 验收标准（Acceptance Criteria）

1. `scan` 输出的 plan：

    * 仅包含 hosted+url 的依赖
    * 满足 `< upper` 规则（当 upper 存在）
    * target 优先 latest，其次 resolvable，再 upgradable
    * 允许从低 minor 升到项目 minor（例如 3.42 -> 3.45.*）

2. `apply` 修改 `pubspec.yaml`：

    * 仅修改 plan 命中的依赖版本字段
    * 文件其他内容逐字保持（包括注释、顺序、缩进、空行）
    * 不生成 `.bak`（仅可能出现短暂 `.tmp`，正常不残留）

3. 升级后 `flutter pub get` 成功（默认行为）

---

如果这份 PRD 符合你预期，我下一步就按它把 `pubspec/pub_upgrade.py` 实现到“scan/apply 都可用”，并确保写入是**严格最小替换**、不会动任何注释/结构。

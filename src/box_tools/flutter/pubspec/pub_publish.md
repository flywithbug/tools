确认一下我理解你新增的发布流程（我会把它融合进 pub_publish 的 PRD）：

* publish 前先做 **git 状态检查** → **git pull 最新**
* 发布前要做 **版本自增**：提示输入（以及 note：回车用默认）
* 版本自增后先跑 **flutter pub get**，没问题再 **git commit（包含 note）**
* 然后再执行 **publish**
* 同时仍保留你之前的质量闸门：**flutter analyze**（error/warning/info 分级处理）

下面是更新后的 **pub_publish PRD（完整）**。

---

# pub_publish 模块 PRD（package 组件自动发布 v2）

## 1. 目标

把 package 发布变成“一条命令跑完”的可靠流水线，做到：

* 发布前自动同步代码、检查环境与质量
* 强制版本自增（可选 minor/patch 或自定义输入），并提交
* `flutter pub get`/`flutter analyze` 把关，发现问题立即中断或按策略确认
* 最后执行 `flutter pub publish`

---

## 2. 关键流程（Publish 主流程）

### 步骤 0：基础校验（快速失败）

* 必须存在 `pubspec.yaml`
* 必须存在 `name:`、`version:`
* 如果发现 `publish_to: none`：

    * 直接阻止发布（提示这是私有包/禁发布）
* 失败：抛出错误并中断

### 步骤 1：检查 Git 工作区是否干净

* 检查 `git status --porcelain`
* 若有未提交变更：

    * 提示：是否中断（交互模式下）
    * `--yes`：默认不中断继续（但要提示风险：可能把无关改动一起提交）
* 若用户选择中断：直接退出（return 0 或 1，建议 1 表示流程未完成）

### 步骤 2：拉取最新代码

* `git pull --ff-only`
* 若失败：抛出错误并中断（提示需要手动处理 rebase/merge）

### 步骤 3：版本自增（必须）

* 读取当前 `pubspec.yaml` 顶层 `version:`
* 提示输入版本升级方式（交互）：

    * 默认：patch 自增（或由你指定默认策略）
    * 可选：minor 自增
    * 可选：手动输入目标版本（将来扩展）
* 版本格式兼容：

    * `3.45.0+2026012103`
    * `3.45.0`
* 自增规则（复用 pub_version 的实现/工具能力）：

    * patch：`3.22.3 -> 3.22.4`（build 号策略按你现有约定）
    * minor：`3.21.* -> 3.22.0`
* 修改原则：只改 `version:` 行，保留注释/结构

> 注意：如果你希望 publish 的版本自增走统一的 `pub_version` 模块，这里 PRD 也支持：pub_publish 调用 `pub_version.bump(...)` 而不是重复实现。

### 步骤 4：输入发布说明 note（用于 commit message / git note）

* 提示输入 note（交互）：

    * 直接回车：使用默认 note（例如 `publish` / `release` / `pub publish` 这类默认文案）
    * 输入文本：作为 note 内容
* note 的用途：

    * 用于 git commit message 的主体（或附加到默认 message 后）
    * （可选扩展）用于 git notes：`git notes add -m ...`（是否启用由你决定；PRD 先以“commit message note”为主，避免依赖 notes 配置）

### 步骤 5：flutter pub get（版本变更后）

* 执行 `flutter pub get`
* 失败：抛出错误并中断（不提交，不 publish）

### 步骤 6：flutter analyze（质量闸门）

* 执行 `flutter analyze`
* 解析输出并分级：

    * **error**：立即失败，中断
    * **warning**：展示全部 warning（或前 N 条 + 总数），提示是否继续
    * **info**：展示数量 + 前 2~3 条样例，提示是否继续
* 交互策略：

    * 交互 + 非 `--yes`：出现 warning/info 需要确认继续
    * `--yes`：默认继续
    * 非交互：默认中断（除非 `--yes`）

> 这里的“warning/info 是否继续”要发生在 publish 之前；一旦用户选择不继续，流程终止且不发布。

### 步骤 7：git commit（版本变更 + lock 变更）

* git add：至少 `pubspec.yaml`，若有 `pubspec.lock` 也加入
* commit

    * subject：默认可为 `pub publish` / `release`（你定）
    * body：包含 note（用户输入或默认）
* 如果你希望 push：

    * push 策略沿用你在 upgrade 里用过的逻辑（有远程分支才 push，否则跳过）

### 步骤 8：执行发布

* `flutter pub publish`（是否加 `--force` 取决于你现有策略）
* 发布失败：抛出错误并中断（此时 commit 已存在，这是符合真实世界的：发布失败需要人工处理）

---

## 3. 子命令与菜单建议

pub_publish 内部建议提供子菜单：

* `publish`：执行上述完整流水线
* `dry-run`：同样跑到 analyze + commit？（建议 dry-run 不 commit，只做校验）

    * 推荐流程：git clean/pull →（可选：不 bump 版本）→ pub get → analyze → `flutter pub publish --dry-run`
* `check`：只做基础校验（pubspec 必填项 + publish_to none + cloudsmith key 等）

---

## 4. 输出与日志规范

沿用你在 pub_upgrade 里的“步骤日志”风格：

* `[1] 检查是否有未提交变更`
* `[2] 拉取最新代码`
* `[3] 版本自增`
* `[4] 输入发布说明 note`
* `[5] flutter pub get`
* `[6] flutter analyze`
* `[7] git commit`
* `[8] flutter pub publish`

原则：

* 无问题尽量简洁
* 有 warning/info 给足够信息做判断（warning 全量 / info 前 2~3 + 总数）
* error 直接失败，给前若干条错误摘要

---

## 5. 退出行为

* 任一步失败：抛异常给 tool.py 捕获并返回 1
* 用户选择中断：返回 0 或 1（建议 1，更利于 CI 判断“未完成”）
* 全流程成功：返回 0

---

如果你认可这版 PRD，我下一步就直接改 `pub_publish.py`：把这些步骤串起来，并复用你 tool.py 的 `Context/run_cmd` 风格，保证和 pub_upgrade 的交互一致、文件数量不增加。



确认一下你新增的两点我按下面规则纳入发布流程：




* publish 开始时必须检查当前目录（项目根）同时存在 **`pubspec.yaml`** 和 **`CHANGELOG.md`**
* **版本自增时必须同步写入 CHANGELOG.md**（新增一条 release 记录）

下面是对 pub_publish PRD 的增补（只写新增/变更部分）。

---

## PRD 增补：文件存在性检查

### 步骤 0（基础校验）新增

* 检查 `pubspec.yaml` 是否存在（原有）
* **新增：检查 `CHANGELOG.md` 是否存在**

    * 不存在：直接报错并中断（提示需要在项目根目录提供 CHANGELOG.md）

输出示例：

* `❌ 未找到 CHANGELOG.md：<path>/CHANGELOG.md（发布要求必须存在）`

---

## PRD 增补：版本自增 + CHANGELOG 更新（绑定）

### 步骤 3（版本自增）扩展为“版本自增 + changelog”

在版本自增成功后，必须更新 `CHANGELOG.md`：

#### 输入要求

* 在“输入发布说明 note”步骤（原步骤 4）里拿到 note 文本：

    * 用户直接回车：使用默认 note（例如 `publish` 或 `release`）
    * 用户输入：用用户输入内容

#### CHANGELOG 写入规则（建议，便于稳定自动化）

* 在 `CHANGELOG.md` 顶部（或 `## [Unreleased]` 下方，如果存在）插入一段：

    * 标题：`## <new_version> - <YYYY-MM-DD>`（日期可取本机日期）
    * 内容：至少一条 bullet，使用 note：

        * `- <note>`
* 不要求改动其它历史内容；尽量只做“插入一段”的最小改动




#### 与 Git 提交绑定




* `git add` 时必须包含：

    * `pubspec.yaml`
    * `CHANGELOG.md`
    * 若存在 `pubspec.lock` 也包含
* commit message body 中包含：

    * `version: <old> -> <new>`
    * `note: <note>`

---

## 发布主流程对应调整（顺序更合理）

为了避免重复输入、以及确保 changelog 一定写上，流程建议变成：

1. 检查未提交变更
2. git pull --ff-only
3. 检查 pubspec.yaml + CHANGELOG.md
4. 版本自增（得到 new_version）
5. 输入 note（空则默认）
6. 更新 CHANGELOG.md（写入 new_version + note）
7. flutter pub get
8. flutter analyze（error/warn/info 分级）
9. git commit（包含 pubspec/changelog/lock）
10. flutter pub publish

---

如果你对 CHANGELOG 的格式已经有团队规范（比如固定 `## [x.y.z]`、或必须写到 `Unreleased` 里、或需要分类 Added/Fixed/Changed），我实现时会优先匹配现有结构，实在找不到才走“顶部插入”的兜底策略。

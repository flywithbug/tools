收到：你要基于现有的 `tool.py` 风格（交互 menu + 子命令分发）实现一个新的 `pubspec` 工具，但**先不写代码**，先把**技术框架（模块拆分、职责边界、调用链、关键抽象）**设计清楚；并新增 `flutter pub publish` 能力，且把三块能力拆成：

* `pub_version.py`
* `pub_upgrade.py`
* `pub_publish.py`

由 `tool.py` 统一调用，顶层菜单为你给的这份。

下面是技术框架设计（按你给的代码结构和交互习惯来）。

---

## 0. 项目结构（建议形态）

```
pubspec/
├── README.md
├── __init__.py
├── tool.py              # CLI 入口：parser + menu + 命令分发
├── doctor.py            # 环境检测（你菜单里有 doctor）
├── io_utils.py          # 统一的文件读写/备份/原子写入/打印样式
├── shell.py             # 执行 flutter/dart/git 命令的封装（可 mock）
├── pub_version.py       # 版本升级（version: x.y.z[+build]）
├── pub_upgrade.py       # 依赖升级（outdated json + pubspec.yaml）
├── pub_publish.py       # 发布（flutter pub publish / dry-run / 校验等）
└── models.py            # 领域模型（可选，但强烈建议）
```

> 你要求“三个功能分三文件”，没问题；`doctor` 建议单独一个文件，避免 tool.py 越长越像意大利面。

---

## 1. tool.py：统一入口与“菜单驱动”的分发层

参考你给的 `box_slang_i18n` 入口模式（`build_parser` + `run_menu` + `main(argv)`），`pubspec/tool.py` 负责四件事：

1. 参数解析（支持非交互直接执行）
2. 定位项目根目录 + 定位 `pubspec.yaml`（默认 `project_root/pubspec.yaml`）
3. menu 交互：选择顶层命令（upgrade/publish/version/doctor）
4. 分发到对应模块，并把公共上下文（路径、dry-run、交互确认函数等）传下去

### 1.1 顶层 menu（你指定的）

```py
menu = [
  ("upgrade", "依赖升级"),
  ("publish", "依赖发布"),
  ("version", "版本升级"),
  ("doctor",  "环境检测"),
]
```

### 1.2 子功能菜单（二级菜单）

每个模块自己提供一个 `run_menu(ctx)`，由 tool.py 进入：

* `version` 子菜单：`patch` / `minor` / `show`
* `upgrade` 子菜单：`scan`（只分析）/ `apply`（落盘）/ `interactive`（逐项确认）
* `publish` 子菜单：`dry-run` / `publish` / `check`（只做发布前校验）
* `doctor` 子菜单：`all` / `flutter` / `git` / `yaml`（可选）

tool.py 只负责“路由”，不要写业务规则。

---

## 2. 统一上下文：Context（避免各模块参数爆炸）

建议定义一个轻量 `Context`（放 `models.py` 或 `tool.py` 内部也行）：

* `project_root: Path`
* `pubspec_path: Path`
* `outdated_json_path: Optional[Path]`（允许用户传入已有 json；否则模块自己去跑命令生成临时文件）
* `interactive: bool`（是否二次确认）
* `dry_run: bool`
* `yes: bool`（跳过所有确认：CI/脚本用）
* `logger/print`（统一输出风格）
* `confirm(prompt) -> bool`（统一交互确认入口）

这样三个模块都只收一个 `ctx`，外加少量专属参数即可。

---

## 3. IO 与安全：写 pubspec.yaml 的“正确姿势”

升级工具最怕两件事：**写坏 yaml** 和 **写一半崩了**。

建议 `io_utils.py` 提供：

* `read_text(path) / write_text_atomic(path, content)`

    * 原子写入：写到 `pubspec.yaml.tmp`，成功后 rename 覆盖
* `backup(path) -> Path`

    * `pubspec.yaml.bak.20260123_153000`
* `load_pubspec_raw(path) -> str`
* `replace_version_line(raw, new_version) -> str`（尽量保持原始格式）
* `update_dependency_block(raw, changes) -> str`（同理尽量局部替换，不重排）

> 关键点：**尽量做“文本级局部替换”**，不要一上来就 YAML parse 再 dump——那会把格式、注释、缩进、排序全毁了。

---

## 4. shell 执行层：所有 flutter/dart/git 命令统一封装

`shell.py` 提供：

* `run(cmd: list[str], cwd: Path, capture=True) -> Completed`
* `which("flutter")` / `check_version("flutter --version")`
* `flutter_pub_outdated_json(cwd) -> dict`
* `flutter_pub_publish(cwd, dry_run=False, force=False, ...)`

目的：

* 业务模块不碰 `subprocess` 的细节
* 更好做 `--dry-run`
* 更好做错误消息规范化（失败原因要清楚）

---

## 5. pub_version.py：版本升级模块（只管 version 行）

### 5.1 职责

* 从 pubspec raw 文本中提取 `version:` 行
* 支持：

    * `3.45.0`
    * `3.45.0+2026012103`
* 执行：

    * patch：`x.y.z -> x.y.(z+1)`
    * minor：`x.y.* -> x.(y+1).0`
* build number 策略：默认**保留** `+build`（与你 PRD 一致）

### 5.2 对外接口（建议）

* `parse_version(raw) -> VersionInfo`
* `bump_patch(v: VersionInfo) -> VersionInfo`
* `bump_minor(v: VersionInfo) -> VersionInfo`
* `apply_version(raw, new_version_str) -> new_raw`
* `run_menu(ctx)` / `run(ctx, mode="patch|minor", apply=True)`

VersionInfo 可以是：

* `major, minor, patch: int`
* `build: Optional[str]`
* `original_line_span`（便于精准替换）

---

## 6. pub_upgrade.py：依赖升级模块（核心：规则决策 + 变更落盘）

你已经给了两份“数据输入”样例：`outdated.json` 和 `pubspec.yaml`。

### 6.1 职责拆分（强烈建议分三层）

1. **采集层**：获得 outdated 数据

    * 优先：调用 `flutter pub outdated --json`
    * 次选：如果 `--outdated-json path` 给了，就直接读

2. **分析层**：比对 pubspec + outdated，生成“变更计划”
   输出一个 `UpgradePlan`，每项包含：

    * package 名称
    * kind（direct/dev/transitive）
    * 当前版本（pubspec 内声明的 / outdated 的 current）
    * latest/resolvable/upgradable
    * 依赖来源：public / hosted(private url) / git / path（本期你重点是 hosted 私有）
    * 决策：`AUTO_APPLY` / `PROMPT` / `SKIP`
    * 原因：例如 `latest minor > app minor`

3. **应用层**：把 plan 落到 pubspec.yaml（文本局部更新）

### 6.2 关键模型（建议）

* `DependencySpec`（从 pubspec 解析出来）

    * name
    * section: dependencies/dev_dependencies/dependency_overrides
    * source_type: version/hosted/git/path/sdk
    * current_constraint (string)：例如 `^1.2.3` 或 `3.1.9` 或 hosted.version
    * hosted_url（如果有）

* `OutdatedItem`（从 json 解析）

    * current/upgradable/resolvable/latest version
    * kind

* `UpgradeDecision`

    * action: auto/prompt/skip
    * target_version
    * reason

* `UpgradePlan`

    * items: list[UpgradePlanItem]
    * summary: counts

### 6.3 决策规则落点（按你 PRD）

* 读取应用版本 `appVersion = X.Y.Z`
* 对每个依赖的 `latest.version = a.b.c`

    * 若 `a.b == X.Y` 或 `a.b < X.Y`：可自动升级（AUTO）
    * 若 `a.b > X.Y` 或 `a > X`：提示升级（PROMPT）
* 私有 hosted 依赖识别：有 `hosted.url` 即视为 private hosted（仍按同规则）

### 6.4 落盘策略（避免破坏结构）

* 只改动被升级的 package 对应的那一小段
* hosted 依赖要写回它的 `version:` 字段
* 普通依赖要写回 `package: <version>` 那行

---

## 7. pub_publish.py：发布模块（flutter pub publish 能力）

这里你说“新增一个 flutter pub publish 的能力”，我按“发布 pub package（可能是私有 hosted）”的语义设计；框架上可做到：

### 7.1 职责

* 发布前校验（不发布也能跑）
* dry-run 发布（如果 flutter 支持/或用 `--dry-run`）
* 实际发布
* 发布后检查（可选）

### 7.2 发布前校验（checklist）

建议至少做这些（doctor 不重复做）：

* `pubspec.yaml` 存在且可读
* `name`, `version` 存在
* `environment.sdk` 存在（可提示但不强制）
* git 工作区是否干净（可配置：允许脏发布 or 强制干净）
* `flutter pub get` 是否需要先跑（你可设：publish 前自动执行）

### 7.3 对外接口（建议）

* `check(ctx) -> CheckResult`
* `dry_run(ctx) -> int`
* `publish(ctx) -> int`
* `run_menu(ctx)`

### 7.4 与 `pub_version` 的衔接

发布通常要求版本已变更（尤其是包发布），所以 publish 菜单可以提供：

* `publish -> 若版本未变化，提示先 version bump`
* 或集成式流程（但你要求分文件，所以“流程编排”放 tool.py 或 pub_publish.py 内部 orchestrate）

---

## 8. doctor：环境检测模块（你菜单中的 doctor）

建议独立 `doctor.py`，职责：

* 检查 `flutter`/`dart` 是否可执行
* 输出版本信息
* 检查 `pubspec.yaml` 是否存在
* 可选：检查当前目录是否 Flutter 项目（是否有 `lib/`, `android/`, `ios/` 等）

对外接口：

* `run(ctx) -> int`
* `run_menu(ctx)`

---

## 9. 错误处理与退出码（CLI 工具必须硬气）

建议统一约定：

* `0`：成功
* `1`：用户输入/交互取消/一般失败
* `2`：环境缺失（flutter 不存在、命令不可用）
* `3`：pubspec 解析失败（version 行异常、依赖段异常）
* `4`：写入失败（权限/磁盘）

并统一打印格式：

* ✅ 成功项
* ⚠️ 风险提示（PROMPT 类）
* ❌ 错误（带下一步建议）

---

## 10. 与你现有风格的“对齐点”（确保像一个家族的工具）

你给的参考 tool.py 特征很明显：

* `BOX_TOOL` 元信息
* `build_parser()` 约束 `choices`
* `menu -> main(argv)` 的递归分发
* `project_root/cfg_path` 这种路径解析套路

`pubspec` 也照这个来：

* `--project-root`
* `--pubspec`（默认 `pubspec.yaml`）
* `--outdated-json`（可选）
* `--dry-run`、`--yes`、`--no-interactive`

并且 menu 的“选项 -> 组装 argv -> 回调 main(argv)”模式可以复用，结构整齐。

---

## 我在这次输出里“做了哪些设计取舍”，为什么这么做（对齐你的习惯）

* 把写文件与业务逻辑拆开：因为 YAML dump 会破坏格式/注释，文本级局部替换更安全。
* 增加 `Context`：因为三个模块共享参数很多（路径、交互、dry-run），不然函数签名会膨胀。
* `pub_upgrade` 明确三层（采集/分析/应用）：因为这是最复杂的模块，必须能“只分析不落盘”，方便 CI 与人工审核。
* `pub_publish` 与 `doctor` 分离：doctor 负责“环境存在”，publish 负责“发布前适配发布语义的校验”，避免重复但也不遗漏。

---

下一步我建议直接输出一份**“模块 API 草案（函数签名 + 数据类）”**，这样你确认后就可以无脑开撸代码而不返工。你如果同意，我会按上述框架把：

* `models.py` 的 dataclass
* 三个模块的 public API
* tool.py 的命令/菜单路由

先写成“接口定义级别”的蓝图（仍不写具体实现）。

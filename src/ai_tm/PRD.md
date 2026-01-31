下面给你一份**详细的技术开发文档（从框架设计 → 最小可跑通 → 接口文档 → 分阶段实现能力）**，完全按你当前约束来写：

* ✅ **复用 box 的 `tool.py / BOX_TOOL / 发布机制`**
* ✅ **不复用 box 里任何已完成业务组件**（扫描/翻译/normalize 等都在 ai_tm 内部自研）
* ✅ 以“先把骨架跑起来 + API 定义稳定”为第一优先

文档会偏“工程说明书”，你照着做就能落地。

---

# ai_tm 本地翻译平台（服务端）技术开发文档

## 0. 约束与原则

### 0.1 生态约束（必须遵守）

1. 工具入口文件必须是 `tool.py`，并导出 `BOX_TOOL` 元数据
2. 发布脚本会扫描 `src/**/tool.py` 并 import 读取 `BOX_TOOL`
3. 除 `tool.py` 外，其他 `.py` 文件不得再出现 `BOX_TOOL`（否则结构校验失败）
   这些是发布机制的硬约束。

`BOX_TOOL` 的字段规范（id/name/category/summary/usage/options/examples/dependencies/docs）遵循统一结构与校验。

### 0.2 架构原则（最佳实践）

* **HTTP 层只做适配，不做业务决策**
* **业务内核全部在 ai_tm 自己的 core/translate/normalize 模块**
* **文件系统即唯一真相**：不引入 DB，不缓存“项目状态”，最多做短时内存缓存（可选）
* 翻译必须**任务化**：可进度、可取消、可重试、可 dry-run

---

## 1. 框架设计

## 1.1 目录结构（推荐）

以工具集标准布局，ai_tm 作为 box_tools 下的一个工具：

```
repo_root/
└─ src/
   └─ box_tools/
      └─ ai_tm/
         ├─ tool.py                  # ✅ 唯一 BOX_TOOL 所在文件
         ├─ __init__.py
         ├─ cli/
         │  └─ server.py             # 启动服务（uvicorn）
         ├─ service/
         │  ├─ app.py                # FastAPI app + 路由挂载
         │  ├─ routes/
         │  │  ├─ health.py
         │  │  ├─ project.py
         │  │  ├─ tasks.py
         │  │  ├─ translate.py
         │  │  └─ normalize.py
         │  └─ dto/
         │     ├─ common.py           # ErrorResponse, Envelope
         │     ├─ project.py
         │     ├─ tasks.py
         │     └─ translate.py
         ├─ core/
         │  ├─ config.py             # 配置加载/校验/模板生成
         │  ├─ scanner.py            # 扫描文件与解析
         │  ├─ repository.py         # 统一文件读写抽象（FS）
         │  └─ analysis.py           # missing/redundant/untranslated 计算
         ├─ i18n/
         │  ├─ strings_parser.py      # .strings 解析/写回（保留注释策略）
         │  └─ json_parser.py         # .json 解析/写回
         ├─ normalize/
         │  └─ normalize.py           # 排序/去重/保留注释
         ├─ translate/
         │  ├─ engine.py              # 翻译任务引擎（调度）
         │  ├─ prompt.py              # prompt 策略
         │  ├─ executor.py            # 模型调用 + 并发 + 重试
         │  └─ validator.py           # 占位符/格式校验
         └─ runtime/
            ├─ state.py               # 内存任务表、锁、取消 token
            └─ ids.py                 # task_id 生成
```

你会发现：

* `tool.py` 只负责 CLI + BOX_TOOL
* `service` 只负责 API
* `core/i18n/translate/normalize` 才是业务内核
  这样既能遵守发布机制，又不会“盒子里长盒子”。

---

## 1.2 依赖建议（最小集）

* `fastapi`
* `uvicorn`
* `pydantic`（FastAPI 会用到）
* （可选）`python-dotenv`：开发态读环境变量
* （可选）`watchdog`：后续做文件变化监听/推送（先别引入）

注意：依赖会被发布脚本自动维护（显式 + import 推断），但建议在 BOX_TOOL.dependencies 里也写清楚。

---

## 2. 最基本开发框架（M1 目标：可启动 + 有 API + 能返回项目状态空壳）

### 2.1 CLI 入口（tool.py）设计

`tool.py` 提供的命令建议：

* `box_ai_tm --help`
* `box_ai_tm server [--host] [--port] [--root] [--reload]`
* `box_ai_tm doctor`（可选：仅做 ai_tm 自己的检查，不复用 box 的 doctor）

其中 `server` 是核心。

`BOX_TOOL` 要写清 usage/options/examples/docs/dependencies。

---

### 2.2 服务启动（cli/server.py）

职责：

* 解析参数
* 构造 FastAPI app（传 root 路径）
* `uvicorn.run(app, ...)`

开发态支持 `--reload`，生产态可关掉。

---

### 2.3 FastAPI app（service/app.py）

职责：

* 创建 `FastAPI(title="ai_tm", version=...)`
* 挂载路由：

  * `/api/health`
  * `/api/project/status`
  * `/api/tasks/*`（先空壳）
  * `/api/translate/*`（先空壳）
  * `/api/normalize`（先空壳）
* 统一异常处理（返回同一错误结构）

---

## 3. 接口文档（先定协议，再填能力）

下面是**建议的最小 API 集**，先让 Web UI 有“稳定 contract”，后续实现只需要填逻辑。

### 3.1 通用约定

#### 3.1.1 统一返回 Envelope（建议）

成功：

```json
{ "ok": true, "data": { ... }, "error": null }
```

失败：

```json
{
  "ok": false,
  "data": null,
  "error": { "code": "CONFIG_MISSING", "message": "xxx", "details": {...} }
}
```

错误码建议分组：

* `CONFIG_*`：配置相关
* `FS_*`：文件系统/路径
* `PARSE_*`：解析失败
* `TRANSLATE_*`：翻译失败
* `TASK_*`：任务状态/取消等

---

### 3.2 Health

#### `GET /api/health`

用途：服务是否活着 + 版本信息

响应：

```json
{
  "ok": true,
  "data": {
    "name": "ai_tm",
    "version": "0.1.0",
    "time": "2026-01-31T12:34:56+08:00"
  },
  "error": null
}
```

---

### 3.3 Project Status（M1 核心）

#### `GET /api/project/status?root=...`（或 root 在启动时固定）

用途：返回项目扫描结果（即使配置缺失也要返回可引导信息）

响应 data（建议）：

```json
{
  "root": "/abs/path",
  "config": {
    "exists": true,
    "path": "/abs/path/ai_tm.yaml",
    "errors": []
  },
  "project": {
    "fileType": "STRINGS",
    "baseLocale": "en",
    "coreLocales": ["zh-Hans", "ja"],
    "nonCoreLocales": ["fr", "de"]
  },
  "summary": {
    "languages": 4,
    "files": 4,
    "missingKeys": 12,
    "redundantKeys": 3,
    "untranslatedKeys": 25
  },
  "files": [
    {
      "language": "zh-Hans",
      "role": "CORE",
      "path": "path/to/Localizable.strings",
      "totalKeys": 120,
      "missingKeys": ["a", "b"],
      "redundantKeys": ["c"],
      "untranslatedKeys": ["d"]
    }
  ],
  "hints": [
    {
      "code": "CONFIG_MISSING",
      "message": "未找到 ai_tm.yaml，可调用 /api/config/template 获取模板"
    }
  ]
}
```

> M1 的“可交付”标准：无论项目是否完整，都能返回结构化信息，让前端可渲染。

---

### 3.4 Config 引导（强烈建议先做）

#### `GET /api/config/template`

用途：返回配置模板（字符串或 JSON）

#### `POST /api/config/init`

用途：在 root 下生成配置文件（如果不存在）

这样 Web UI 才能做到“一键初始化”。

---

### 3.5 Translate（M2 做闭环：plan → execute → commit）

#### `POST /api/translate/plan`

输入：

```json
{
  "mode": "incremental",
  "scope": "core",
  "allowOverwrite": false
}
```

输出：

```json
{
  "changes": [
    {
      "language": "zh-Hans",
      "willTranslateKeys": ["k1", "k2"],
      "willSkipKeys": ["k3"],
      "reason": { "k3": "already_translated" }
    }
  ],
  "total": 123
}
```

#### `POST /api/translate/execute`

输入同 plan，返回 task_id：

```json
{ "taskId": "t_abc123" }
```

#### `GET /api/tasks/{taskId}`

输出：

```json
{
  "taskId": "t_abc123",
  "status": "running",
  "progress": { "done": 10, "total": 123 },
  "errors": [
    { "key": "k9", "language": "ja", "message": "placeholder_mismatch" }
  ]
}
```

#### `POST /api/tasks/{taskId}/cancel`

取消任务。

#### `POST /api/translate/commit`

输入：

```json
{ "taskId": "t_abc123" }
```

输出：写回影响的文件列表、keys 数量。

> commit 独立出来是最佳实践：防止“翻译一半就落盘”，也让 dry-run 和 UI review 成为可能。

---

### 3.6 Normalize & Redundant（M3）

#### `POST /api/normalize`

输入：

```json
{ "targets": ["all"] }
```

输出：修改了哪些文件、做了哪些动作（排序/去重）

#### `GET /api/redundant`

输出：按语言列出 base 不存在但其他语言存在的 key，并给出建议（但不自动删）

---

## 4. 分阶段实现路线（一步步填能力）

### Milestone 1：骨架可跑 + 状态可查（建议 1～2 天内完成）

**目标**

* `box_ai_tm server` 能启动
* `/api/health` 正常
* `/api/project/status` 返回结构化信息
* 配置缺失时有引导（template + init）

**实现清单**

* [ ] tool.py：BOX_TOOL + argparse 子命令
* [ ] FastAPI app + routes + DTO
* [ ] core/config.py：读取/校验/模板生成
* [ ] core/scanner.py：先实现“发现文件”与“空解析”
* [ ] 错误模型 + 统一 Envelope

**验收**

* 在空目录启动，status 能明确提示“缺配置/缺文件”
* 在有配置但无文件时，提示缺文件
* 在有文件但解析未完成时，至少能列出文件路径

---

### Milestone 2：解析器 + 分析能力（missing/redundant/untranslated）

**目标**

* `.strings` 解析出 key/value/注释结构
* `.json` 解析出 key/value
* 能计算 missing/redundant/untranslated

**实现清单**

* [ ] i18n/strings_parser.py：读取 + 写回（先写回可延后）
* [ ] i18n/json_parser.py
* [ ] core/analysis.py：对齐你定义的三类 keys
* [ ] status API 返回真实统计

**验收**

* 能对一个真实 iOS Localizable.strings 工程给出正确统计

---

### Milestone 3：翻译引擎（plan/execute/task/cancel/commit）

**目标**

* 任务表（内存）
* dry-run plan 可用
* execute 后可查询进度
* commit 才落盘

**实现清单**

* [ ] runtime/state.py：任务存储、锁、取消 token
* [ ] translate/engine.py：组装任务、拆 batch、汇总结果
* [ ] translate/executor.py：并发、重试（先简单）
* [ ] translate/prompt.py：结构化 JSON 输出
* [ ] translate/validator.py：占位符一致性、JSON 结构校验
* [ ] routes/tasks.py + translate.py

**验收**

* 翻译失败只影响单 key，不导致任务整体崩溃
* cancel 能停止后续 batch
* commit 写回可追踪

---

### Milestone 4：normalize + 冗余处理建议 + Web UI 对接增强

**目标**

* normalize 能排序/去重/保留注释
* redundant API 给出建议（不自动删）
* 可选：SSE 推送任务进度（Web UI 更顺滑）

---

## 5. 开发态调试建议（落地性很关键）

### 5.1 本地启动

* `box_ai_tm server --root . --port 8765 --reload`

### 5.2 PyCharm 飘红 fastapi 的常见原因（你之前遇到过）

* 解释器不是 pipx venv 的解释器
* 或项目 venv 没装 fastapi/uvicorn

最佳实践：

* 给 ai_tm 单独建一个 dev venv（用于开发）
* 发布时依赖由 pyproject 维护；运行时用 pipx 安装后的环境

> 你之前“Unresolved reference fastapi”基本就是解释器/依赖环境没对上，不是代码问题。

---

## 6. 与发布机制的对接（你只需要做到这些）

* 确保 `src/box_tools/ai_tm/tool.py` 存在且可 import
* `BOX_TOOL` 字段齐全
* 依赖在 import 中出现（发布脚本会推断并写入 dependencies）

---

# 你接下来我建议的“最优先落地顺序”

1. 先做 **M1：最小骨架 + status API + config template/init**
2. 再做 **M2：解析 + 分析**
3. 然后 **M3：翻译任务闭环**
4. 最后 **M4：normalize/冗余/SSE**

这样每一步都能交付、可演示、可被 Web UI 接起来。

---

如果你同意按这个节奏走，下一步我会直接输出 **M1 的“工程骨架实现清单”**（包含具体文件列表、每个文件的职责、函数签名、DTO 字段定义），让你可以直接开始写代码而不是再讨论结构。

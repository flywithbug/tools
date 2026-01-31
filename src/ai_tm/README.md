# box_ai_tm 本地服务端项目框架（完整导出）

> 目标：遵循 tools 工具集约定（`tool.py` + `BOX_TOOL`），实现一个**轻量**本地服务端：  
> - **不使用数据库**  
> - **以本地文件为真相来源**（扫描后分析/修复/翻译）  
> - 支持两种翻译文件格式：`*.strings` 与 `*.json`  
> - CLI 命令：`box_ai_tm server` 启动服务（可托管 WebUI 静态页）

---

## 1. 总体形态

- **CLI 工具**：`box_ai_tm`（console script）  
  - `tool.py` 为入口，必须导出 `BOX_TOOL`（供 `box` 工具集扫描/展示）
- **本地 HTTP 服务**：FastAPI + uvicorn
  - 提供 `/api/*` 接口：workspace / analyze / fix / translate / tasks / events（SSE 可选）
- **文件系统为真相**  
  - 服务端不持久化业务状态（可用内存缓存短期结果）
  - UI 偏好、选择等可存浏览器（localStorage/IndexedDB）

---

## 2. 代码目录（建议/落地版）

> 说明：你当前仓库里 `src/ai_tm/` 已存在（截图所示）。以下以该路径为准。  
> 若你未来要统一到 `src/box_tools/*` 也可以平移（入口脚本与 import 路径同步调整即可）。

```
src/
  ai_tm/
    __init__.py
    README.md
    tool.py                    # CLI 入口（BOX_TOOL + argparse）
    web_ui/                    # (可选) 静态页面（你当前目录名是 web_ui）
      index.html
      app.js
      styles.css

    server/
      __init__.py              # 必须存在：保证 from .server.xxx 相对导入可用
      app.py                   # create_app(...)
      routes.py                # API 路由注册（按 format 分流）
      models.py                # Pydantic 模型 + 枚举（Enum）
      workspace.py             # 工作区解析：配置/targets/locale 列表/格式探测
      webui.py                 # 静态资源挂载（匹配 web_ui/ 或 webui/static/）
      tasks.py                 # (轻量) 任务表/进度（内存）
      events.py                # (可选) SSE 事件推送

      formats/
        __init__.py
        base.py                # 格式适配接口（load/analyze/write）
        strings_fmt.py         # .strings 适配器（解析/写回）
        json_fmt.py            # .json 适配器（flatten/unflatten/写回）
```

---

## 3. CLI 与工具集约定

### 3.1 tool.py 关键点

- 必须导出 `BOX_TOOL`（dict），通过 `_share.tool_spec` 的 `tool/opt/ex` 构造。
- 提供命令：`box_ai_tm server`  
  - `--workspace`（默认 `.`）  
  - `--host`（默认 `127.0.0.1`）  
  - `--port`（默认 `37123`）  
  - `--open` 启动后打开浏览器  
  - `--no-webui` 禁用静态页面托管（纯 API）

### 3.2 pyproject.toml 入口点

- console script 需包含：

```toml
[project.scripts]
box_ai_tm = "ai_tm.tool:main"
```

- dependencies 至少包含：

```toml
[project]
dependencies = [
  "fastapi",
  "uvicorn",
]
```

---

## 4. 服务端 API（契约优先，功能后实现）

> 统一用 `format=strings|json` 做分流，避免拆太多 endpoint。

### 4.1 基础

- `GET /api/health` → `HealthResponse`
- `GET /api/workspace` → `WorkspaceInfoResponse`

Workspace 返回：
- 工作区路径、配置状态
- 探测到的 formats（strings/json 可能同时存在）
- base_locale / locales / core_locales / noncore_locales（来自配置或默认）

### 4.2 扫描/分析

- `GET /api/analyze?format=strings|json` → `AnalyzeResponse`

输出包含：
- `files`：每个 locale 的文件信息（path/mtime/key_count）
- `summary`：
  - missing_keys_by_locale
  - redundant_keys_by_locale
  - duplicate_keys_by_locale
  - total_keys_by_locale

### 4.3 修复（写回）

- `POST /api/fix` → `FixResponse`  
  body：`FixRequest`
  - `actions`: `[sort, dedupe, remove_redundant, normalize]`
  - `keys`: remove_redundant 时可指定要删的 keys（空则删全部冗余）

可选：
- `POST /api/fix/preview`（不落盘，仅返回预览结果）

### 4.4 翻译（先占位，返回任务引用）

- `POST /api/translate` → `TranslateResponse`  
  body：`TranslateRequest`
  - `mode`: incremental/full
  - `scope`: core/noncore/all
  - `keys`/`incremental_keys`（可选）

### 4.5 任务与事件

- `GET /api/tasks/{task_id}` → `TaskStatusResponse`
- `GET /api/events`（SSE，可选）
  - `workspace.updated`
  - `analyze.updated`
  - `task.updated`

---

## 5. 枚举与模型（统一放 server/models.py）

### 5.1 枚举（Enum）

- `I18nFormat`: `strings` | `json`
- `TaskStatus`: queued/running/success/failed/canceled
- `FixAction`: sort/dedupe/remove_redundant/normalize
- `TranslateMode`: incremental/full
- `Scope`: core/noncore/all

### 5.2 主要模型（Pydantic）

- `HealthResponse`
- `WorkspaceInfoResponse`
- `AnalyzeResponse`（含 `AnalyzeSummary`、`LocaleFileInfo`）
- `FixRequest` / `FixResponse`（含 `FixResult`）
- `TranslateRequest` / `TranslateResponse`（含 `TaskRef`）
- `TaskStatusResponse`（含 `TaskProgress`）

---

## 6. 工作区规则（轻量默认 + 配置覆盖）

### 默认（无配置也能跑）
- targets 默认：`<workspace>/i18n`
- base_locale 默认：`en`
- `.strings` 默认：
  - `<targets>/<locale>.lproj/Localizable.strings`
- `.json` 默认常见候选（自动探测）：
  - `<targets>/<locale>.json`
  - `<targets>/locales/<locale>.json`
  - `<targets>/i18n/<locale>.json`

### 配置存在时（strings_i18n.yaml）
- 覆盖 baseLocale、targetsPath、fileName、core/noncore 列表等
- 配置不存在时提供引导（前端/接口均可提示）

---

## 7. formats 适配层（两种格式共用 analyze/fix/translate 流程）

### 7.1 base.py（接口）
- `detect(workspace) -> bool/paths`
- `list_locales(workspace) -> [locale]`
- `load(locale) -> dict[key,value]`
- `write(locale, map, options) -> changed`
- `analyze(all_maps, base_locale) -> missing/redundant/duplicate/summary`

### 7.2 strings_fmt.py
- 解析：最小可用 `"KEY" = "VALUE";`
- 修复：去重/排序/删冗余写回
- 后续增强：保留注释与块结构（你之前的硬要求）

### 7.3 json_fmt.py
- 支持扁平 & 嵌套：
  - 读取后 flatten 成 `a.b.c`
  - 写回时按原风格 unflatten（或统一扁平，按配置决定）
- 修复：去重（覆盖策略记录）、排序（稳定输出）、删冗余

---

## 8. WebUI 托管（可选但推荐）

- 默认 root `/` 托管静态页
- 若静态目录不存在，返回兜底页面（链接到 `/api/health` `/api/workspace`）

> 你当前目录名是 `web_ui/`，注意 `server/webui.py` 里挂载路径要与其对齐。

---

## 9. 里程碑（建议）

- Milestone A：骨架跑通
  - `box_ai_tm server` 启动
  - health/workspace 正常
  - WebUI 兜底页
- Milestone B：分析闭环（analyze）
  - 支持 json/strings 探测与 analyze 输出
- Milestone C：修复闭环（fix）
  - sort/dedupe/remove_redundant 写回
- Milestone D：翻译闭环（translate）
  - 先 task stub → 再接现有 openai_translate 批处理

---

## 10. 你当前已确认的关键约束（不会忘）

- 本地服务端不需要数据库  
- 主要流程：扫描本地文件 → 处理 → 写回  
- 多语言翻译文件类型：`*.json` 与 `*.strings`  
- 工具集约定：`tool.py` + `BOX_TOOL`  
- `box_ai_tm server` 为启动命令  
- 打包安装：`pipx install --force "git+https://github.com/flywithbug/tools.git"`

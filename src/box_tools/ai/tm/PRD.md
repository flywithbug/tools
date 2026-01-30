# 本地翻译管理平台 PRD
**Local Translation Manager – Local Service + Local Web UI**

---

## 1. 产品背景与目标

### 1.1 背景
在多语言项目中，开发者通常依赖脚本或零散工具完成以下工作：

- 管理多语言配置
- 扫描多语言文件
- 执行排序、去重、冗余检查
- 调用 AI 进行增量翻译

这些能力往往缺乏统一的状态管理与可视化反馈，难以持续使用与维护。

### 1.2 产品目标
构建一个**完全本地运行**的翻译管理平台，提供：

- 本地服务（Local Service）作为唯一状态与逻辑中心
- 本地 Web 页面（Local Web UI）作为可视化操作界面
- 对当前工作目录内多语言文件的统一管理能力

---

## 2. 产品定位

- 本地工具（Local-first）
- 非 SaaS、非远程服务
- 面向开发者 / 本地工程
- CLI 启动 + 浏览器访问
- 强调确定性、可恢复性、可审计性

一句话定位：

> 一个“带 Web UI 的本地翻译管理工具”，而不是一个在线翻译平台。

---

## 3. 使用方式

### 3.1 启动方式
```bash
cd <project-root>
box_ai_tm serve
```

- 启动目录即 Workspace Root
- 所有扫描与写回操作仅允许在该目录树内

### 3.2 访问方式
- 浏览器访问：`http://127.0.0.1:<port>`

---

## 4. 整体架构

```
┌────────────────────┐
│   Browser (UI)     │
│  原生 HTML/CSS/JS  │
└─────────┬──────────┘
          │ HTTP / SSE
          ▼
┌──────────────────────────┐
│   本地 FastAPI 服务      │
│                          │
│ - Workspace 管理         │
│ - 配置识别与生成         │
│ - 文件扫描与规范化       │
│ - 冗余检查               │
│ - 翻译任务（Job）        │
│ - SSE 事件推送           │
│ - UI 静态资源托管        │
└─────────┬────────────────┘
          ▼
┌──────────────────────────┐
│     本地文件系统         │
│                          │
│ - 翻译配置 YAML          │
│ - 多语言文件             │
│ - 本地状态（SQLite）     │
└──────────────────────────┘
```

---

## 5. 核心设计原则

### 5.1 状态唯一来源
- 所有“文件状态”“翻译状态”“是否需要生成/冗余”等判断
- **必须由本地服务端统一计算并维护**
- 前端页面不得自行扫描文件系统或推断状态

### 5.2 数据同步模型
- 页面加载：HTTP 获取全量状态快照
- 状态变化：SSE 实时推送事件
- 前端根据事件刷新 UI 或重新拉取快照

---

## 6. Workspace 与启动引导

### 6.1 Workspace 定义
- Workspace Root = 服务启动时的当前目录
- 所有操作路径必须在 Workspace Root 内
- 禁止路径越界（`..`）

### 6.2 Doctor 启动诊断
服务启动后自动执行环境诊断，判断：

- 是否存在翻译配置文件
- 配置是否可解析
- 是否存在可翻译文件
- 是否存在待翻译内容

### 6.3 引导状态
- `config_missing`：未发现配置文件
- `no_files_found`：有配置但未发现翻译文件
- `nothing_to_translate`：文件存在但无待翻译项
- `ready`：可正常操作

---

## 7. 本地服务（Backend）需求

### 7.1 配置管理
- 自动识别配置文件（优先支持 `slang_i18n.yaml`）
- 校验配置合法性
- 支持生成默认配置

### 7.2 文件扫描（Scan）
- 根据配置扫描指定目录
- 识别 source / target 文件对
- 统计：
  - key 总数
  - missing 数
  - checksum
- 解析错误需明确指出文件与原因

### 7.3 文件规范化（Normalize）
- 排序
- 去重（重复 key）
- 稳定化输出
- 支持 dry-run 与实际写回
- 返回变更摘要

### 7.4 冗余检查（Redundant）
- 查找 base 中不存在但 target 中存在的 key
- 生成冗余报告
- 支持批量删除

### 7.5 翻译执行（Translate）
- 增量翻译（仅缺失或空值）
- 支持 batch 翻译
- placeholder 校验
- 多文件并发、单文件互斥写回

### 7.6 文件写回
- 写回前校验 source 是否变化
- 原子写入（tmp → rename）
- 自动生成 `.bak` 备份

---

## 8. Job 系统与实时推送

### 8.1 Job 模型
- job_id
- job_type（scan / normalize / redundant / translate）
- status（pending / running / success / failed）
- progress（0–100）
- 日志（流式）

### 8.2 SSE 推送
- 提供全局事件流：
  ```
  GET /api/events
  ```
- 事件类型包括：
  - scan_started / scan_done
  - normalize_done
  - redundant_check_done
  - translate_progress
  - translate_done
  - state_changed
  - toast
  - ping

---

## 9. 本地文件变化监控

### 9.1 监控范围
- Workspace Root 下的配置文件
- 配置中指定的目标文件夹（如 i18nDir）

### 9.2 行为规则
- 监听文件新增/修改/删除
- debounce 合并事件（推荐 ~800ms）
- 触发重新扫描（scan）
- 通过 SSE 推送 `state_changed`

---

## 10. 本地前端页面（Frontend）需求

### 10.1 技术约束
- 原生 HTML / CSS / JavaScript
- 不使用前端框架
- 不使用 TypeScript
- 不使用构建工具

### 10.2 页面模块
- Workspace / 配置状态栏
- 操作区：
  - 扫描
  - 规范化
  - 冗余检查
  - 翻译
- 文件列表表格
- Job 进度与日志区

### 10.3 前端职责边界
- 不直接读取本地文件
- 不自行推断状态
- 所有数据来自本地服务 API / SSE

---

## 11. API 概览

### 状态与引导
- `GET /api/state`
- `GET /api/doctor`

### 启动引导
- `POST /api/bootstrap/config`
- `POST /api/bootstrap/sample`

### 操作
- `POST /api/scan`
- `POST /api/normalize`
- `POST /api/redundant/check`
- `POST /api/redundant/apply`
- `POST /api/translate`

### 实时推送
- `GET /api/events`（SSE）

---

## 12. 非功能性需求

- 单个任务失败不影响服务整体运行
- 状态可恢复、可重复执行
- 文件操作安全、可回滚
- 易于扩展新的文件类型与翻译引擎

---

## 13. 产品总结

这是一个以**本地文件系统为核心**、  
以**本地服务为唯一状态与逻辑中心**、  
通过 **SSE 驱动前端实时更新** 的本地翻译管理平台。

它强调的是：  
**确定性、可控性、可持续使用，而不是一次性脚本。**

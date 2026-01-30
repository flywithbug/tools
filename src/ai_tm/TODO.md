好，这一步非常关键 👍
我会把你这份 **《本地翻译管理平台_整体_PRD》** 拆成一套**工程可执行的里程碑（Milestones）**，每个里程碑都有：

* 目标（完成后“能做什么”）
* 涉及模块（后端 / 前端）
* 明确的验收标准（能不能过，一眼就知道）

**不掺杂实现细节**，但足够指导你往下推进，不会走偏。

---

# 本地翻译管理平台 · 开发里程碑拆解

> 原则：
> **每个里程碑结束时，系统必须“能跑 + 有可见价值”**
> 而不是“写了一堆但还不能用”。

---

## Milestone 0：基础工程骨架（已完成 / 基本完成）

### 目标

* 本地服务可启动
* 本地前端页面可访问
* 前后端通信打通

### 包含内容

**Backend**

* `box_ai_tm serve` CLI
* FastAPI 本地服务
* `/api/state`
* `/api/events`（SSE 心跳）
* EventBus

**Frontend**

* 原生 HTML/CSS/JS 页面
* 能显示 workspace 路径
* 能接收 SSE 连接

### 验收标准

* 在任意目录运行 `box_ai_tm serve`
* 浏览器打开页面不报错
* SSE 不断线（能看到 ping / hello）

---

## Milestone 1：Doctor + 启动引导（配置/示例生成）

### 目标

* 服务能判断“当前目录是否可用”
* 空目录也能被引导到“可翻译状态”

### 包含内容

**Backend**

* `GET /api/doctor`
* `POST /api/bootstrap/config`
* `POST /api/bootstrap/sample`
* Doctor 状态机：

    * `config_missing`
    * `no_files_found`
    * `ready`

**Frontend**

* 引导卡片（根据 doctor.status 切换）
* 按钮：

    * 生成默认配置
    * 生成示例文件

### 验收标准

* 空目录启动 → 提示生成配置
* 生成配置后 → 提示生成示例
* 生成示例后 → 状态变为 ready
* 全流程无需手动改文件

---

## Milestone 2：Scan（文件扫描与状态建模）

### 目标

* 系统真正“认识”多语言文件
* 前端能看到文件级状态

### 包含内容

**Backend**

* `POST /api/scan`
* 扫描配置指定目录
* 构建 `files[]`：

    * source / target
    * locale
    * key_count
    * missing_count
    * needs_generate
* 扫描完成推送 `state_changed`

**Frontend**

* 文件列表表格
* 显示 missing 数、是否缺 target
* 扫描按钮

### 验收标准

* 点击扫描 → 有 job 日志
* 扫描完成 → 文件列表出现
* 修改配置 → 重新扫描生效

---

## Milestone 3：文件监听（Watch + 自动刷新）

### 目标

* 文件一改，页面自动更新
* 本地体验接近“桌面应用”

### 包含内容

**Backend**

* 监听：

    * workspace 下配置文件
    * 配置指定的 i18nDir
* debounce（~800ms）
* 自动触发 scan
* SSE 推送 `state_changed`

**Frontend**

* 无需按钮，自动刷新状态

### 验收标准

* IDE 中保存配置文件 → 页面自动刷新
* IDE 中修改翻译文件 → missing 数变化
* 不出现 scan 风暴（频繁触发）

---

## Milestone 4：Normalize（排序 / 去重 / 稳定化）

### 目标

* 让文件“可维护”
* 消除 diff 噪音

### 包含内容

**Backend**

* `POST /api/normalize`
* dry-run / write-back
* 规则：

    * `.strings`：保留注释 / 分组
    * `.json`：稳定排序
* 变更摘要

**Frontend**

* 规范化按钮
* 显示“将修改多少文件 / key”
* 完成后自动刷新状态

### 验收标准

* 执行规范化 → 文件排序变化正确
* 再次执行 → 无变化（幂等）
* Git diff 干净可读

---

## Milestone 5：冗余检查（Redundant）

### 目标

* 发现并管理“多余翻译”
* 避免脏数据长期堆积

### 包含内容

**Backend**

* `POST /api/redundant/check`
* `POST /api/redundant/apply`
* 计算：

    * base 不存在、target 存在的 key
* 冗余报告

**Frontend**

* 冗余提示 badge
* 冗余列表查看
* 批量删除按钮

### 验收标准

* 能准确列出冗余 key
* 删除后文件与状态一致
* 不误删 base 中存在的 key

---

## Milestone 6：Translate（增量 AI 翻译）

### 目标

* 真正完成“翻译管理”的核心价值

### 包含内容

**Backend**

* `POST /api/translate`
* 增量翻译（missing / empty）
* batch 处理
* placeholder 校验
* 多文件并发、单文件互斥

**Frontend**

* 翻译按钮（全部 / 单文件）
* 实时进度条
* 实时日志（SSE）

### 验收标准

* 翻译过程可视化
* 翻译完成 → missing 归零
* placeholder 未被破坏

---

## Milestone 7：稳定性与工程收尾

### 目标

* 从“能用”变成“能长期用”

### 包含内容

**Backend**

* 错误恢复
* Job 重试
* 写回冲突保护
* SQLite 持久化（可选）

**Frontend**

* 错误提示友好
* 状态异常可恢复

### 验收标准

* 翻译失败不会挂服务
* 重启服务状态可恢复
* 用户不需要重来一遍流程

---

## 总体推进建议（非常重要）

* **不要并行做多个里程碑**
* 每个里程碑：

    1. 后端完成
    2. 前端最小支持
    3. 手动验收
    4. 再进入下一个

---

### 现在你已经在的位置

* Milestone 0：完成
* Milestone 1：后端基本完成（Doctor + Bootstrap）

👉 **下一步建议：完整收尾 Milestone 1（前端引导 + 后端联调），然后直接进入 Milestone 2（Scan）**。

如果你愿意，我可以**直接按 Milestone 2 的粒度**，给你下一步的：

* 数据结构设计
* 扫描算法
* API 返回示例
* 对应前端展示字段

你只需要说一句：**“继续 Milestone 2”**。

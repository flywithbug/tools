好，进入 **Milestone 3：文件监听（Watch + 自动刷新）**。这一里程碑的目标很明确：

> **监控 workspace 根目录下的配置文件 + 配置里的 i18nDir 目录**
> 有变化就 debounce 合并 → 自动触发一次 scan → SSE 推送 `state_changed` → 前端自动刷新

下面给你一套“能直接落地”的后端实现方案（不碰前端，前端只要继续监听 `state_changed` 拉 `/api/state` 就行）。

---

# Milestone 3 交付与验收

## 交付物

1. 服务启动后自动开启文件监听
2. 监听范围：

* `<workspace>/slang_i18n.yaml`
* `<workspace>/<i18nDir>/**`

3. debounce：推荐 800ms（可配置）
4. 事件推送：

* `watch_started`
* `watch_reload`（配置变化导致 watcher 重建）
* `watch_event`（可选，调试用）
* `scan_started / scan_done`
* `state_changed`

## 验收标准

* 修改 `slang_i18n.yaml` 保存 → 800ms 内自动 scan → 页面自动更新
* 修改 `i18nDir` 内任一 `.json/.strings` 保存 → 自动 scan → 页面自动更新
* 连续保存 10 次 → 只触发少量 scan（debounce 生效，不风暴）

---

# 技术选型：watchdog（推荐）

你需要新增依赖：

* `watchdog`

在 `pyproject.toml` 的 dependencies 里加：

```toml
watchdog>=4.0
```

---

# 代码结构（新增一个 WatchService）

新增文件：

```
src/box_tools/ai/tm/services/watch.py
```

并修改：

* `app.py`：启动/关闭时启动 watcher；提供触发 scan 的回调

---

# Step 1：实现 WatchService（services/watch.py）

下面这份实现满足：

* 监听 config + i18nDir
* debounce 合并事件
* config 变化会 reload i18nDir watcher
* 支持忽略目录（可后续扩展）

```python
# src/box_tools/ai/tm/services/watch.py
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Set

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from .config import load_slang_config, CONFIG_FILENAME


@dataclass
class WatchTargets:
    config_path: Path
    i18n_dir: Optional[Path]


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        on_fs_event: Callable[[Path], None],
        only_suffixes: Optional[Set[str]] = None,
        ignore_dirs: Optional[Set[str]] = None,
    ) -> None:
        super().__init__()
        self.on_fs_event = on_fs_event
        self.only_suffixes = only_suffixes or set()
        self.ignore_dirs = ignore_dirs or set()

    def _should_ignore(self, p: Path) -> bool:
        # 忽略目录（按名称）
        for part in p.parts:
            if part in self.ignore_dirs:
                return True
        if self.only_suffixes:
            if p.suffix.lower() not in self.only_suffixes:
                return True
        return False

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        try:
            p = Path(event.src_path)
        except Exception:
            return
        if self._should_ignore(p):
            return
        self.on_fs_event(p)


class WatchService:
    """
    监听：
      1) workspace 根目录下的配置文件（slang_i18n.yaml）
      2) 配置里的 i18nDir 目录

    触发策略：
      - debounce 合并事件后触发回调（通常触发 scan）
      - 配置变化会触发 reload（重建 i18nDir watcher）
    """

    def __init__(
        self,
        root: Path,
        debounce_ms: int = 800,
        on_trigger: Optional[Callable[[str], None]] = None,
        suppress_window_ms: int = 1500,
    ) -> None:
        self.root = root
        self.debounce_ms = debounce_ms
        self.on_trigger = on_trigger or (lambda reason: None)

        # 防止写回导致自触发：在 suppress 窗口内忽略事件
        self.suppress_window_ms = suppress_window_ms
        self._suppress_until = 0.0

        self._lock = threading.Lock()
        self._pending = False
        self._pending_reason = "fs_change"
        self._timer: Optional[threading.Timer] = None

        self._observer = Observer()
        self._config_watch_path = self.root  # 监听根目录，过滤文件名
        self._i18n_watch_path: Optional[Path] = None

        self._ignore_dirs = {".git", "node_modules", "dist", "build", ".box_ai_tm"}
        self._only_suffixes = {".yaml", ".yml", ".json", ".strings"}

    def suppress_events_for_writeback(self) -> None:
        """在写回前调用，避免 watcher 把自己写回当成外部变化。"""
        with self._lock:
            self._suppress_until = time.time() + (self.suppress_window_ms / 1000.0)

    def _is_suppressed(self) -> bool:
        return time.time() < self._suppress_until

    def _schedule(self, reason: str) -> None:
        with self._lock:
            if self._is_suppressed():
                return
            self._pending = True
            self._pending_reason = reason
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_ms / 1000.0, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if not self._pending:
                return
            reason = self._pending_reason
            self._pending = False
            self._timer = None
        self.on_trigger(reason)

    def _current_targets(self) -> WatchTargets:
        cfg_path = self.root / CONFIG_FILENAME
        cfg = load_slang_config(self.root)
        i18n_dir = cfg.i18n_dir if cfg else None
        return WatchTargets(config_path=cfg_path, i18n_dir=i18n_dir)

    def start(self) -> None:
        # 监听 workspace 根目录（用过滤判断 slang_i18n.yaml）
        def on_root_event(p: Path) -> None:
            # 只关心配置文件本身
            if p.name == CONFIG_FILENAME:
                # 配置变化：先触发 reload（重建 i18n watcher）再触发 scan
                self.reload()
                self._schedule("config_changed")

        root_handler = _Handler(
            on_fs_event=on_root_event,
            only_suffixes={".yaml", ".yml"},
            ignore_dirs=self._ignore_dirs,
        )
        self._observer.schedule(root_handler, str(self._config_watch_path), recursive=False)

        # 监听 i18nDir（如果存在）
        self._attach_i18n_watcher()

        self._observer.start()

    def _attach_i18n_watcher(self) -> None:
        targets = self._current_targets()
        if not targets.i18n_dir:
            self._i18n_watch_path = None
            return
        i18n_dir = targets.i18n_dir
        # 目录不存在也没关系：等 bootstrap/sample 创建后会产生事件，但 watcher 不在就收不到
        # 所以：如果目录不存在，我们仍然监听它的父目录，用于捕获“目录创建”。
        watch_path = i18n_dir if i18n_dir.exists() else i18n_dir.parent
        self._i18n_watch_path = i18n_dir

        def on_i18n_event(p: Path) -> None:
            # 只关心 i18nDir 内的文件变化（如果监听的是 parent，需要额外过滤）
            if self._i18n_watch_path and self._i18n_watch_path not in p.parents and p != self._i18n_watch_path:
                return
            self._schedule("i18n_changed")

        i18n_handler = _Handler(
            on_fs_event=on_i18n_event,
            only_suffixes={".json", ".strings"},
            ignore_dirs=self._ignore_dirs,
        )
        self._observer.schedule(i18n_handler, str(watch_path), recursive=True)

    def reload(self) -> None:
        """
        配置变了可能导致 i18nDir 改变。
        watchdog 的 Observer 不太适合“精确移除某个 handler”，
        所以这里用简化策略：停止并重启 observer。
        对本地工具完全够用，且最稳。
        """
        try:
            self.stop()
        except Exception:
            pass

        # 重新建 observer（避免 handler 堆叠）
        self._observer = Observer()
        self.start()

        # 配置 reload 也应触发一次 scan
        self._schedule("watch_reload")

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
                self._pending = False
        try:
            self._observer.stop()
            self._observer.join(timeout=2)
        except Exception:
            pass
```

---

# Step 2：把 watcher 接到 FastAPI（app.py）

关键点：watchdog 回调在**线程**里触发，你的 SSE publish 是 async。
最稳的做法是：在 app 启动时抓住 event loop，然后用 `asyncio.run_coroutine_threadsafe()` 把事件扔回主循环。

修改 `src/box_tools/ai/tm/app.py`：

1. 在 `create_app()` 里初始化 `scan_cache`、watch service
2. 在 startup 时启动 watcher
3. 在 shutdown 时停止 watcher
4. watcher 触发时：调用 scan（你在 Milestone 2 已经有 `scan_workspace`）

示例（把关键段落贴给你）：

```python
# 关键：添加这些 import
import asyncio
from .services.watch import WatchService
from .services.scanner import scan_workspace
```

在 `create_app()` 内添加：

```python
app.state.scan_cache = None
app.state.watch_service = None
```

加 startup / shutdown（FastAPI 旧版写法也能用）：

```python
@app.on_event("startup")
async def _startup():
    loop = asyncio.get_running_loop()
    root = app.state.workspace_root
    bus = app.state.eventbus

    def trigger(reason: str):
        # 线程回调：丢回主事件循环处理
        async def _do():
            await bus.publish("global", {"type": "watch_event", "reason": reason})
            await bus.publish("global", {"type": "scan_started", "reason": reason})

            scan_cache, errors = scan_workspace(root)
            app.state.scan_cache = scan_cache

            await bus.publish(
                "global",
                {
                    "type": "scan_done",
                    "reason": reason,
                    "summary": scan_cache.get("summary", {}),
                    "errors_count": len(errors),
                },
            )
            await bus.publish("global", {"type": "state_changed"})

        asyncio.run_coroutine_threadsafe(_do(), loop)

    ws = WatchService(root=root, debounce_ms=800, on_trigger=trigger)
    app.state.watch_service = ws
    ws.start()
    await bus.publish("global", {"type": "watch_started", "root": str(root)})


@app.on_event("shutdown")
async def _shutdown():
    ws = getattr(app.state, "watch_service", None)
    if ws:
        ws.stop()
```

> 注意：这里把“watch 触发 scan”的逻辑直接放在 watcher 里了，省得再走 HTTP 调 `/api/scan`。
> 这样内部链路最短、最稳，前端只管等 `state_changed`。

---

# Step 3：和 bootstrap 生成联动（很重要）

你在 Milestone 1 的 `bootstrap/config` 和 `bootstrap/sample` 会写文件。
为了避免 watcher 把它当成“外部变更”导致二次 scan 风暴，建议在 bootstrap 写文件前短暂 suppress：

在 `api/bootstrap.py` 的写文件前加：

```python
ws = getattr(request.app.state, "watch_service", None)
if ws:
    ws.suppress_events_for_writeback()
```

同样，未来 normalize/translate 写回也用这个。

---

# Step 4：Milestone 3 验收步骤（推荐你按这个测）

1. 空目录启动服务
2. 调用：

* `POST /api/bootstrap/config`
* `POST /api/bootstrap/sample`

3. **不手动点 scan**，直接改文件触发：

* 修改 `slang_i18n.yaml`（比如改 locales）保存
  → 800ms 内自动 scan → `/api/state` files 变化
* 修改 `i18n/zh-Hans.json` 写入一个 key 保存
  → 自动 scan → missing_count 下降

你在前端事件区会看到：

* `watch_started`
* `watch_event`
* `scan_started`
* `scan_done`
* `state_changed`

---

# 常见坑（我已经帮你规避了）

* 编辑器保存产生多次事件 → debounce 合并 ✅
* i18nDir 改了 → watcher reload ✅
* 生成/写回触发自扫描 → suppress window ✅
* watchdog 线程回调里不能 await → run_coroutine_threadsafe ✅

---

如果你愿意，我下一步就把 **Milestone 4（Normalize）** 的后端落地方案给你：

* dry-run + write-back
* JSON flat 的稳定排序/去重
* `.strings` 作为第二插件接入（你已有 iOS strings 体系的话更顺）

好，继续 **Milestone 2：Scan（文件扫描与状态建模）**。这一里程碑的核心是：**本地服务真正“认识”你的多语言文件**，能算出 `missing_count / needs_generate / key_count`，并通过 SSE 推送 `state_changed` 让前端自动刷新。

下面我按“直接落代码”的方式给你完整方案（后端优先，前端只要按已有机制刷新 `/api/state` 就行）。

---

# Milestone 2 目标

## 交付物

1. `POST /api/scan`：触发扫描，更新服务端缓存状态
2. `/api/state`：返回 `files[]` 与 `summary.files_found / pending_translate_items` 等
3. SSE 推送：

    * `scan_started`
    * `scan_done`
    * `toast`
    * `state_changed`

## 扫描范围（先做最通用）

* 先实现 **JSON flat**（`i18n/en.json`, `i18n/zh-Hans.json` 这种）
* iOS `.strings` 可以作为下一步扩展（不影响 M2 验收）

---

# 后端实现设计（简单但可扩展）

## 1) 服务端缓存结构（state cache）

不要每次 `/api/state` 都扫盘。M2 建议：

* `POST /api/scan` 扫一次 → 写入 `app.state.scan_cache`
* `/api/state` 直接读 cache（没有 cache 就返回空）

### 建议 cache 格式

```python
app.state.scan_cache = {
  "last_scan_at": 1234567890.0,
  "files": [ ... ],
  "summary": { ... },
  "errors": [ ... ],
}
```

---

# 需要新增的文件

新增：

```
src/box_tools/ai/tm/services/scanner.py
src/box_tools/ai/tm/api/scan.py
```

并修改：

* `api/state.py`（让它合并 scan_cache）
* `app.py`（注册 scan router、初始化 scan_cache）

---

# 2) 扫描器：services/scanner.py（JSON flat MVP）

```python
# src/box_tools/ai/tm/services/scanner.py
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .config import load_slang_config


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        # 解析错误由上层收集
        raise


def _flat_keys(d: Dict[str, Any]) -> Dict[str, str]:
    # M2：只处理 flat JSON：key -> string
    # 非 string 的值先转成 str（或直接忽略）可以后续再严谨化
    # 这里先做：仅保留 str 值
    out: Dict[str, str] = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = v
    return out


def _missing_count(base: Dict[str, str], target: Dict[str, str]) -> int:
    # 缺失规则：不存在 or 空字符串
    n = 0
    for k, v in base.items():
        tv = target.get(k, None)
        if tv is None or (isinstance(tv, str) and tv.strip() == ""):
            n += 1
    return n


def scan_workspace(root: Path) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """
    返回：(scan_cache, errors)
    scan_cache 包含 files[] 与 summary 等。
    """
    errors: List[Dict[str, str]] = []
    cfg = load_slang_config(root)
    now = time.time()

    if cfg is None:
        return (
            {
                "last_scan_at": now,
                "files": [],
                "summary": {"files_found": 0, "pending_translate_items": 0},
                "errors": [{"code": "CONFIG_NOT_FOUND", "message": "未找到 slang_i18n.yaml"}],
            },
            errors,
        )

    i18n_dir = cfg.i18n_dir
    base_locale = cfg.base_locale
    locales = cfg.locales

    base_path = (i18n_dir / f"{base_locale}.json").resolve()

    # base 读取
    base_raw: Dict[str, Any] = {}
    if base_path.exists():
        try:
            base_raw = _load_json(base_path)
        except Exception as e:
            errors.append({"code": "JSON_PARSE_ERROR", "message": f"{base_path}: {e}"})
    else:
        errors.append({"code": "BASE_NOT_FOUND", "message": f"base 文件不存在：{base_path}"})

    base_map = _flat_keys(base_raw)

    files: List[Dict[str, Any]] = []
    pending_total = 0

    for loc in locales:
        target_path = (i18n_dir / f"{loc}.json").resolve()

        target_raw: Dict[str, Any] = {}
        if target_path.exists():
            try:
                target_raw = _load_json(target_path)
            except Exception as e:
                errors.append({"code": "JSON_PARSE_ERROR", "message": f"{target_path}: {e}"})
        target_map = _flat_keys(target_raw)

        missing = _missing_count(base_map, target_map) if base_map else 0
        pending_total += missing

        files.append(
            {
                "id": f"json:{loc}",
                "plugin": "json_flat",
                "locale": loc,
                "source_path": str(base_path),
                "target_path": str(target_path),
                "target_exists": target_path.exists(),
                "needs_generate": not target_path.exists(),
                "key_count": len(base_map),
                "missing_count": missing,
            }
        )

    summary = {
        "files_found": len(files),
        "pending_translate_items": pending_total,
        "errors_count": len(errors),
    }

    scan_cache = {
        "last_scan_at": now,
        "config": {
            "type": "slang_i18n",
            "path": str(cfg.path),
            "i18nDir": str(i18n_dir),
            "baseLocale": base_locale,
            "locales": locales,
        },
        "files": files,
        "summary": summary,
        "errors": errors,
    }
    return scan_cache, errors
```

> M2 的要点就是先把 **files[] + missing_count** 算出来。
> 之后扩展 `.strings` 也只是新增一个 plugin 扫描器，并把结果 append 进 `files[]`。

---

# 3) 扫描 API：api/scan.py

```python
# src/box_tools/ai/tm/api/scan.py
from __future__ import annotations

from fastapi import APIRouter, Request
from ..services.scanner import scan_workspace

router = APIRouter(prefix="/api", tags=["scan"])


@router.post("/scan")
async def scan(request: Request):
    root = request.app.state.workspace_root
    bus = request.app.state.eventbus

    await bus.publish("global", {"type": "scan_started"})
    scan_cache, errors = scan_workspace(root)

    # 写入缓存
    request.app.state.scan_cache = scan_cache

    await bus.publish(
        "global",
        {
            "type": "toast",
            "level": "success" if not errors else "warning",
            "message": "扫描完成" if not errors else f"扫描完成（有 {len(errors)} 个问题）",
        },
    )
    await bus.publish("global", {"type": "scan_done", "summary": scan_cache.get("summary", {})})
    await bus.publish("global", {"type": "state_changed"})

    return {"ok": True, "summary": scan_cache.get("summary", {}), "errors": errors}
```

---

# 4) 修改 /api/state：合并 scan_cache

```python
# src/box_tools/ai/tm/api/state.py
from fastapi import APIRouter, Request
from ..services.state import build_state

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state")
def get_state(request: Request):
    root = request.app.state.workspace_root
    base = build_state(root)

    scan_cache = getattr(request.app.state, "scan_cache", None)
    if not scan_cache:
        return base

    # 合并
    base["scan"] = {
        "last_scan_at": scan_cache.get("last_scan_at"),
        "errors": scan_cache.get("errors", []),
    }
    base["files"] = scan_cache.get("files", [])
    # 用 scan summary 覆盖 base summary 的部分字段
    base["summary"].update(scan_cache.get("summary", {}))

    return base
```

---

# 5) 修改 app.py：注册 scan router + 初始化 scan_cache

在 `create_app()` 里：

```python
from .api.scan import router as scan_router
```

然后：

```python
app.state.scan_cache = None
app.include_router(scan_router)
```

---

# Milestone 2 验收清单（后端）

在一个空目录：

1. `POST /api/bootstrap/config`
2. `POST /api/bootstrap/sample`（会生成 i18n/en.json + i18n/zh-Hans.json）
3. `POST /api/scan`
4. `GET /api/state`

你应该看到：

* `files` 数量 = locales 数量（默认 1）
* 每个 file 有：

    * `key_count`（base key 数）
    * `missing_count`（target 缺失/空值数）
    * `needs_generate`（target 不存在则 true）

然后你手动编辑 `i18n/zh-Hans.json` 填入部分 key，再 `POST /api/scan`，`missing_count` 会下降。

---

# 前端对接（保持最小改动）

你当前前端已经监听 `state_changed` 并刷新 `/api/state`。
所以 M2 的 UI 只要加一个“扫描按钮”调用 `POST /api/scan` 就能跑通（下一步再做表格显示 files）。

---

如果你按上面把文件加进去，Milestone 2 的后端就基本完成了。
下一步（Milestone 3）我们就接 **watchdog 监听配置文件 + i18nDir**，自动触发 scan + debounce，让页面“你一保存文件它就刷新”。

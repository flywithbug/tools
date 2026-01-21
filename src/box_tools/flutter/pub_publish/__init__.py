from __future__ import annotations

try:
    from importlib.metadata import version as _version

    # 注意：这里的 "box" 是包的 import 名，不一定等于 distribution 名。
    # 版本读取失败会兜底，不影响功能。
    __version__ = _version("box")
except Exception:
    __version__ = "0.0.0"

try:
    from .cli import main  # noqa: F401
except Exception:
    # 避免安装/构建阶段 import 顺序导致的问题
    pass

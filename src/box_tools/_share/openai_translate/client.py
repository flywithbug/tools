from __future__ import annotations

import os
import sys
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from getpass import getpass

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


class OpenAIConfigError(RuntimeError):
    pass


_EXPORT_RE = re.compile(r"^\s*export\s+OPENAI_API_KEY\s*=\s*.*$")


def _is_interactive() -> bool:
    # stdin / stderr 都是 TTY 才认为可交互（更稳，避免管道/重定向）
    return sys.stdin.isatty() and sys.stderr.isatty()


def _pick_profile_for_shell() -> Path:
    """
    按 $SHELL 选择写入的 profile 文件：
    - zsh  -> ~/.zshrc
    - bash -> ~/.bash_profile
    - fish -> ~/.config/fish/config.fish
    """
    shell = (os.getenv("SHELL") or "").strip().lower()
    home = Path.home()

    if shell.endswith("zsh"):
        return home / ".zshrc"
    if shell.endswith("bash"):
        return home / ".bash_profile"
    if shell.endswith("fish"):
        return home / ".config" / "fish" / "config.fish"

    # Fallback for unknown shells
    return home / ".bash_profile"


def _upsert_to_shell_profile(api_key: str, profile_path: Path) -> None:
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    line = f'export OPENAI_API_KEY="{api_key}"\n'

    if profile_path.exists():
        lines = profile_path.read_text(encoding="utf-8", errors="ignore").splitlines(
            keepends=True
        )
    else:
        lines = []

    replaced = False
    out = []
    for ln in lines:
        if ln.lstrip().startswith("export OPENAI_API_KEY=") or _EXPORT_RE.match(ln):
            out.append(line)
            replaced = True
        else:
            out.append(ln)

    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append("\n# Added by box_tools\n")
        out.append(line)

    profile_path.write_text("".join(out), encoding="utf-8")


def resolve_api_key(api_key: Optional[str] = None) -> str:
    key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        return key

    # 找不到 key：非交互式保持原行为，别卡死
    if not _is_interactive():
        raise OpenAIConfigError(
            "未检测到 OpenAI API Key。\n"
            "请先在终端配置环境变量：\n"
            '  export OPENAI_API_KEY="sk-***"\n'
            "或在调用时显式传入 api_key。"
        )

    # 交互式：提示输入并写入 shell 对应的 profile，然后提示 source 并退出
    print("未检测到 OPENAI_API_KEY。", file=sys.stderr)
    entered = getpass("请输入 OpenAI API Key（输入不回显）： ").strip()
    if not entered:
        raise OpenAIConfigError("未输入 API Key，已退出。")

    # 轻量校验：避免把空白字符写进去
    if any(c.isspace() for c in entered):
        raise OpenAIConfigError("API Key 包含空白字符，看起来不太对；请重新输入。")

    profile_path = _pick_profile_for_shell()
    _upsert_to_shell_profile(entered, profile_path)

    print(f"已写入 {profile_path}", file=sys.stderr)
    print(f"请先执行：source {profile_path}", file=sys.stderr)
    print("然后重新运行当前命令以生效。", file=sys.stderr)
    raise SystemExit(0)


@dataclass(frozen=True)
class OpenAIClientFactory:
    timeout: float = 30.0

    def create(self, api_key: Optional[str] = None) -> "OpenAI":  # type: ignore
        if not OpenAI:
            raise SystemExit("OpenAI SDK 未安装，请先 pip install openai>=1.0.0")
        key = resolve_api_key(api_key)
        return OpenAI(api_key=key, timeout=self.timeout)

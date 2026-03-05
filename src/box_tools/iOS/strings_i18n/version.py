from __future__ import annotations

import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from . import data

_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _relpath(p: Path, base: Path) -> str:
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except Exception:
        return str(p.resolve())


def _load_plist(path: Path) -> Tuple[Dict, int]:
    raw = path.read_bytes()
    fmt = plistlib.FMT_BINARY if raw.startswith(b"bplist") else plistlib.FMT_XML
    obj = plistlib.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("plist 顶层必须是 dict")
    return obj, fmt


def _write_plist(path: Path, obj: Dict, fmt: int) -> None:
    data_bytes = plistlib.dumps(obj, fmt=fmt, sort_keys=False)
    path.write_bytes(data_bytes)


def _parse_version(version: str) -> List[int]:
    if not _VERSION_RE.match(version):
        raise ValueError("版本号格式无效，需为数字与点（如 1.2.3）")
    return [int(x) for x in version.split(".")]


def _bump_minor(version: str) -> str:
    parts = _parse_version(version)
    if len(parts) == 1:
        parts.append(0)
    parts[1] += 1
    if len(parts) >= 3:
        parts[2] = 0
    else:
        parts.append(0)
    return ".".join(str(x) for x in parts)


def _bump_patch(version: str) -> str:
    parts = _parse_version(version)
    if len(parts) == 1:
        parts.append(0)
    if len(parts) == 2:
        parts.append(0)
    parts[2] += 1
    return ".".join(str(x) for x in parts)


def _choose_base_version(unique_versions: List[str]) -> str:
    if len(unique_versions) == 1:
        return unique_versions[0]
    print("\n检测到多个不同版本，请选择作为基准：")
    for idx, v in enumerate(unique_versions, start=1):
        print(f"{idx}. {v}")
    choice = input("输入序号（默认 1）：").strip()
    if not choice:
        return unique_versions[0]
    if not choice.isdigit():
        raise ValueError("无效选择")
    idx = int(choice)
    if not (1 <= idx <= len(unique_versions)):
        raise ValueError("无效选择")
    return unique_versions[idx - 1]


def run_version_bump(cfg: data.StringsI18nConfig) -> int:
    if not sys.stdin.isatty():
        print("需要交互输入，请在终端运行此命令")
        return 1

    repo_root = _git_root(cfg.project_root)
    if not repo_root:
        print("未检测到 git 仓库，无法执行自动提交")
        return 1

    if _git_is_dirty(repo_root):
        print("git 工作区不干净，请先提交/清理现有改动后再运行")
        return 1

    plist_paths = cfg.info_plist_paths or []
    if not plist_paths:
        print("未配置 info_plist_paths")
        print("请在 strings_i18n.yaml 中配置 Info.plist 列表")
        return 1

    missing: List[Path] = []
    versions: List[Tuple[Path, str]] = []

    for p in plist_paths:
        if not p.exists():
            missing.append(p)
            continue
        try:
            obj, _fmt = _load_plist(p)
            v = obj.get("CFBundleShortVersionString")
            if not isinstance(v, str) or not v.strip():
                v = ""
            versions.append((p, v))
        except Exception as e:
            print(f"读取失败：{p}（{e}）")
            return 1

    if missing:
        print("以下 Info.plist 不存在：")
        for p in missing:
            print(f"- {p}")
        return 1

    print("\n当前 CFBundleShortVersionString：")
    for p, v in versions:
        disp = v if v else "<缺失>"
        print(f"- {_relpath(p, cfg.project_root)}: {disp}")

    empty = [p for p, v in versions if not v]
    if empty:
        print("\n存在缺失版本号的 Info.plist，请先修复：")
        for p in empty:
            print(f"- {_relpath(p, cfg.project_root)}")
        return 1

    unique_versions = sorted({v for _p, v in versions})
    try:
        base_version = _choose_base_version(unique_versions)
    except Exception as e:
        print(f"选择失败：{e}")
        return 1

    print("\n请选择升级方式：")
    print("1. 增加path版本（次版本 +1，补丁清零）")
    print("2. 增加补丁版本（patch +1）")
    print("3. 输入自定义版本")
    choice = input("输入 1/2/3（默认 1）：").strip() or "1"

    try:
        if choice == "1":
            new_version = _bump_minor(base_version)
        elif choice == "2":
            new_version = _bump_patch(base_version)
        elif choice == "3":
            custom = input("请输入版本号（如 1.2.3）：").strip()
            if not custom:
                raise ValueError("版本号不能为空")
            _ = _parse_version(custom)
            new_version = custom
        else:
            print("无效选择")
            return 1
    except Exception as e:
        print(f"版本号处理失败：{e}")
        return 1

    for p, _v in versions:
        try:
            obj, fmt = _load_plist(p)
            obj["CFBundleShortVersionString"] = new_version
            _write_plist(p, obj, fmt)
            print(f"✅ 已更新：{_relpath(p, cfg.project_root)} -> {new_version}")
        except Exception as e:
            print(f"写入失败：{p}（{e}）")
            return 1

    changed = _git_status_porcelain(repo_root)
    if not changed:
        print("未检测到 git 变更，跳过提交")
        return 0

    plist_rel = [_relpath(p, repo_root) for p in plist_paths]
    if not _git_add(repo_root, plist_rel):
        return 1

    msg = f"chore: bump iOS version to {new_version}"
    if not _git_commit(repo_root, msg):
        return 1

    if not _git_push(repo_root):
        return 1

    print("✅ 已提交并推送")
    return 0


def _git_root(start: Path) -> Path | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            stderr=subprocess.STDOUT,
        ).decode("utf-8", "replace")
    except Exception:
        return None
    p = Path(out.strip())
    return p if p.exists() else None


def _git_status_porcelain(repo_root: Path) -> List[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            stderr=subprocess.STDOUT,
        ).decode("utf-8", "replace")
    except Exception:
        return []
    return [line for line in out.splitlines() if line.strip()]


def _git_is_dirty(repo_root: Path) -> bool:
    return len(_git_status_porcelain(repo_root)) > 0


def _git_add(repo_root: Path, paths: List[str]) -> bool:
    try:
        subprocess.check_call(["git", "-C", str(repo_root), "add", *paths])
        return True
    except Exception as e:
        print(f"git add 失败：{e}")
        return False


def _git_commit(repo_root: Path, msg: str) -> bool:
    try:
        subprocess.check_call(["git", "-C", str(repo_root), "commit", "-m", msg])
        return True
    except Exception as e:
        print(f"git commit 失败：{e}")
        return False


def _git_push(repo_root: Path) -> bool:
    try:
        subprocess.check_call(["git", "-C", str(repo_root), "push"])
        return True
    except Exception as e:
        print(f"git push 失败：{e}")
        return False

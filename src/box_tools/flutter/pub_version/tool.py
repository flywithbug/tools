from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BOX_TOOL = {
    "id": "flutter.pub_version",           # 唯一标识（建议：领域.工具名）
    "name": "box_pub_version",             # ✅ 命令名（按规范统一加 box_ 前缀）
    "category": "flutter",
    "summary": "升级 Flutter pubspec.yaml 的 version（支持交互选择 minor/patch，可选 git 提交）",
    "usage": [
        "box_pub_version",
        "box_pub_version minor",
        "box_pub_version patch --no-git",
        "box_pub_version minor --file path/to/pubspec.yaml",
    ],
    "options": [
        {"flag": "--file", "desc": "指定 pubspec.yaml 路径（默认 ./pubspec.yaml）"},
        {"flag": "--no-git", "desc": "只改版本号，不执行 git add/commit/push"},
    ],
    "examples": [
        {"cmd": "box_pub_version", "desc": "进入交互菜单选择升级级别"},
        {"cmd": "box_pub_version patch --no-git", "desc": "仅更新补丁号，不提交"},
    ],
    # ✅ 约定：docs 永远写 README.md（相对工具目录），由汇总脚本按文件所在目录解析
    "docs": "README.md",
}


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\+(.+))?$")


def parse_version(version: str):
    m = _VERSION_RE.match(version.strip())
    if not m:
        raise ValueError("version 格式应为 x.y.z 或 x.y.z+build")
    major, minor, patch = map(int, m.group(1, 2, 3))
    build = m.group(4)
    return major, minor, patch, build


def format_version(major: int, minor: int, patch: int, build: str | None):
    v = f"{major}.{minor}.{patch}"
    return f"{v}+{build}" if build else v


def bump(major: int, minor: int, patch: int, level: str):
    if level == "minor":
        return major, minor + 1, 0
    if level == "patch":
        return major, minor, patch + 1
    raise ValueError("level 必须是 minor 或 patch")


def read_version(content: str) -> str:
    # ✅ 排除注释行：# version: ...
    m = re.search(r"(?m)^(?!\s*#)\s*version:\s*([^\s]+)\s*$", content)
    if not m:
        raise ValueError("pubspec.yaml 中未找到 version")
    return m.group(1).strip()


def replace_version(content: str, new_version: str) -> str:
    return re.sub(
        r"(?m)^(?!\s*#)(\s*version:\s*)([^\s]+)\s*$",
        lambda m: f"{m.group(1)}{new_version}",
        content,
        count=1,
    )


def is_git_repo(cwd: Path) -> bool:
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        return p.stdout.strip().lower() == "true"
    except Exception:
        return False


def git_commit(pubspec_path: Path, version: str) -> bool:
    """
    返回 True 表示 git add/commit/push 都成功；
    False 表示失败（但文件可能已经更新）。
    """
    cwd = pubspec_path.parent

    # ✅ 不在 git 仓库：视为“跳过 git”，不算失败
    if not is_git_repo(cwd):
        print("ℹ️ 当前目录不是 git 仓库，已跳过 git 操作（等同 --no-git）")
        return True

    try:
        subprocess.run(["git", "add", str(pubspec_path)], cwd=str(cwd), check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore(pub): bump version to {version}"],
            cwd=str(cwd),
            check=True,
        )
        subprocess.run(["git", "push"], cwd=str(cwd), check=True)
        return True
    except FileNotFoundError:
        print("⚠️ 未找到 git 命令，已跳过 git 操作（等同 --no-git）")
        return True
    except subprocess.CalledProcessError:
        return False


def choose_level_interactive(current_version: str, preview_minor: str, preview_patch: str) -> str:
    print(f"📦 当前版本: {current_version}")
    print("请选择升级级别：")
    print(f"1 - 次版本号（minor）升级 → {preview_minor}")
    print(f"2 - 补丁号（patch）升级 → {preview_patch}")
    print("0 - 退出")
    choice = input("请输入 0 / 1 / 2（或 q 退出）: ").strip().lower()

    if choice in ("0", "q", "quit", "exit"):
        raise SystemExit(0)  # 正常退出

    if choice == "1":
        return "minor"
    if choice == "2":
        return "patch"

    raise ValueError("无效输入（只能是 0/1/2 或 q）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box_pub_version",
        description="升级 Flutter pubspec.yaml 中的 version（支持交互选择 minor/patch）",
    )
    p.add_argument(
        "level",
        nargs="?",
        choices=["minor", "patch"],
        help="升级级别（不填则进入交互选择）",
    )
    p.add_argument("--file", default="pubspec.yaml", help="pubspec.yaml 路径（默认 ./pubspec.yaml）")
    p.add_argument("--no-git", action="store_true", help="不执行 git add/commit/push")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"❌ 文件不存在: {path}")
        return 2

    content = path.read_text(encoding="utf-8")
    try:
        old = read_version(content)
        major, minor, patch, build = parse_version(old)
    except Exception as e:
        print(f"❌ 解析失败: {e}")
        return 2

    # 预览两种升级后的版本（用于交互提示）
    minor_v = format_version(*bump(major, minor, patch, "minor"), build)
    patch_v = format_version(*bump(major, minor, patch, "patch"), build)

    level = args.level
    if not level:
        try:
            level = choose_level_interactive(old, minor_v, patch_v)
        except SystemExit as e:
            # 用户主动退出（0）
            return int(getattr(e, "code", 0) or 0)
        except Exception as e:
            print(f"❌ {e}")
            return 2

    new_major, new_minor, new_patch = bump(major, minor, patch, level)
    new_version = format_version(new_major, new_minor, new_patch, build)

    if new_version == old:
        print(f"ℹ️ 版本未变化: {old}")
        return 0

    print(f"🔼 {old} → {new_version}")

    new_content = replace_version(content, new_version)
    path.write_text(new_content, encoding="utf-8")
    print(f"✅ 已更新: {path}")

    if args.no_git:
        print("ℹ️ 已跳过 git 操作（--no-git）")
        return 0

    if git_commit(path, new_version):
        print("✅ git commit & push 完成（或已自动跳过）")
        return 0

    print("⚠️ git 操作失败（版本已更新）")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)  # 130 = SIGINT 的惯例退出码

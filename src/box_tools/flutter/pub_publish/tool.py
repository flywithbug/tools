from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import subprocess
import sys
from pathlib import Path


BOX_TOOL = {
    "id": "flutter.box_pub_publish",
    "name": "box_pub_publish",
    "category": "flutter",
    "summary": "自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，提交并发布（支持 release 分支规则）",
    "usage": [
        "box_pub_publish --msg fix crash on iOS",
        "box_pub_publish --msg feat add new api --no-publish",
        "box_pub_publish --pubspec path/to/pubspec.yaml --changelog path/to/CHANGELOG.md --msg release notes",
        "box_pub_publish --msg hotfix --dry-run",
    ],
    "options": [
        {"flag": "--pubspec", "desc": "pubspec.yaml 路径（默认 ./pubspec.yaml）"},
        {"flag": "--changelog", "desc": "CHANGELOG.md 路径（默认 ./CHANGELOG.md）"},
        {"flag": "--msg", "desc": "更新说明（必填；可写多段，不需要引号）"},
        {"flag": "--no-pull", "desc": "跳过 git pull"},
        {"flag": "--no-git", "desc": "跳过 git add/commit/push（若不是 git 仓库也会自动跳过）"},
        {"flag": "--no-publish", "desc": "跳过 flutter pub publish"},
        {"flag": "--skip-pub-get", "desc": "跳过 flutter pub get"},
        {"flag": "--dry-run", "desc": "仅打印将执行的操作，不改文件、不跑命令"},
    ],
    "examples": [
        {"cmd": "box_pub_publish --msg fix null error", "desc": "拉代码→升级版本→更新 changelog→pub get→提交→发布"},
        {"cmd": "box_pub_publish --msg release notes --no-publish", "desc": "只提交不发布"},
        {"cmd": "box_pub_publish --msg try --dry-run", "desc": "预演一次，不做任何修改"},
    ],
    # ✅ 新项目规范：工具目录内 README.md（相对当前目录）
    "docs": "README.md",
}


class CmdError(RuntimeError):
    pass


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def is_git_repo(cwd: Path) -> bool:
    if not which("git"):
        return False
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
        return p.stdout.strip().lower() == "true"
    except Exception:
        return False


def run_command(cmd: list[str], *, dry_run: bool = False, cwd: Path | None = None) -> None:
    """运行外部命令；失败则抛异常（携带 stderr）。"""
    if dry_run:
        print("🧪 DRY-RUN:", " ".join(cmd))
        return

    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if p.returncode != 0:
        msg = (
            f"执行命令失败: {' '.join(cmd)}\n"
            f"exit code: {p.returncode}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}\n"
        )
        raise CmdError(msg)


def get_current_branch(*, dry_run: bool = False) -> str:
    if dry_run:
        return "(dry-run)"
    p = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return p.stdout.strip()


def git_pull(*, dry_run: bool = False) -> None:
    print("🔄 git pull ...")
    run_command(["git", "pull"], dry_run=dry_run)
    print("✅ 代码已更新")


def parse_semver(version: str):
    """支持 x.y.z 或 x.y.z+build（build 原样保留）。"""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\+(.+))?$", version.strip())
    if not m:
        raise ValueError("version 格式应为 x.y.z 或 x.y.z+build")
    major, minor, patch = map(int, m.group(1, 2, 3))
    build = m.group(4)
    return major, minor, patch, build


def format_semver(major: int, minor: int, patch: int, build: str | None) -> str:
    base = f"{major}.{minor}.{patch}"
    return f"{base}+{build}" if build else base


def compare_versions(a: str, b: str) -> int:
    """比较两个 x.y.z（忽略 build），返回 -1/0/1。"""
    am, an, ap, _ = parse_semver(a)
    bm, bn, bp, _ = parse_semver(b)
    if (am, an, ap) < (bm, bn, bp):
        return -1
    if (am, an, ap) > (bm, bn, bp):
        return 1
    return 0


def update_version(current_version: str, branch_version: str | None) -> str:
    """版本升级策略：

    - release-<x.y.z> 分支：
      - 若当前 < 分支版本：直接提升到分支版本（保留 build）
      - 若当前 >= 分支版本：patch + 1（保留 build）

    - 非 release 分支：patch + 1（保留 build）
    """
    major, minor, patch, build = parse_semver(current_version)

    if branch_version:
        cur_base = format_semver(major, minor, patch, None)
        cmp = compare_versions(cur_base, branch_version)
        if cmp < 0:
            bm, bn, bp, _ = parse_semver(branch_version)
            return format_semver(bm, bn, bp, build)
        return format_semver(major, minor, patch + 1, build)

    return format_semver(major, minor, patch + 1, build)


def extract_project_name(pubspec_path: Path) -> str:
    content = pubspec_path.read_text(encoding="utf-8")
    m = re.search(r"^\s*name\s*:\s*['\"]?([\w\-\.]+)['\"]?\s*$", content, flags=re.MULTILINE)
    return m.group(1) if m else "unknown"


def update_pubspec_preserve_format(pubspec_path: Path, *, dry_run: bool = False) -> tuple[str, str]:
    content = pubspec_path.read_text(encoding="utf-8")

    # 版本号支持 x.y.z 或 x.y.z+build；引号可选；保持原有引号；排除注释行
    pattern = (
        r"^(?!\s*#)"
        r"(?P<prefix>\s*version\s*:\s*)"
        r"(?P<quote>['\"]?)"
        r"(?P<version>\d+\.\d+\.\d+(?:\+[^\s'\"]+)?)"
        r"(?P=quote)"
        r"\s*$"
    )
    m = re.search(pattern, content, flags=re.MULTILINE)
    if not m:
        raise ValueError("未在 pubspec.yaml 中找到 version 字段")

    current_version = m.group("version")

    current_branch = get_current_branch(dry_run=dry_run)
    branch_version: str | None = None
    br = re.match(r"^release-(\d+\.\d+\.\d+)$", current_branch)
    if br:
        branch_version = br.group(1)

    new_version = update_version(current_version, branch_version)

    replacement = f"{m.group('prefix')}{m.group('quote')}{new_version}{m.group('quote')}"
    new_content = re.sub(pattern, replacement, content, count=1, flags=re.MULTILINE)

    print(f"🔼 版本号: {current_version} -> {new_version}")

    if dry_run:
        print(f"🧪 DRY-RUN: 将写入 {pubspec_path}")
        return new_version, current_version

    pubspec_path.write_text(new_content, encoding="utf-8")
    print(f"✅ 已更新: {pubspec_path}")
    return new_version, current_version


def update_changelog(changelog_path: Path, new_version: str, msg: str, *, dry_run: bool = False) -> None:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"## {new_version}\n\n- {now}\n- {msg}\n\n"

    if dry_run:
        print(f"🧪 DRY-RUN: 将更新 {changelog_path}（在文件头插入新版本区块）")
        return

    if not changelog_path.exists():
        changelog_path.write_text(header, encoding="utf-8")
    else:
        old = changelog_path.read_text(encoding="utf-8")
        changelog_path.write_text(header + old, encoding="utf-8")

    print(f"✅ CHANGELOG.md 已更新: {changelog_path}（版本 {new_version}）")


def git_commit(pubspec_path: Path, changelog_path: Path, project_name: str, new_version: str, *, dry_run: bool = False) -> None:
    msg = f"build: {project_name} + {new_version}"

    paths = [str(pubspec_path), str(changelog_path)]
    lock_path = pubspec_path.with_name("pubspec.lock")
    if lock_path.exists():
        paths.append(str(lock_path))

    print("📝 git add ...")
    run_command(["git", "add", *paths], dry_run=dry_run)

    print("📝 git commit ...")
    run_command(["git", "commit", "-m", msg], dry_run=dry_run)

    print("🚀 git push ...")
    run_command(["git", "push"], dry_run=dry_run)

    print(f"✅ 已提交并推送: {msg}")


def flutter_pub_get(*, dry_run: bool = False) -> None:
    print("🧩 flutter pub get ...")
    run_command(["flutter", "pub", "get"], dry_run=dry_run)
    print("✅ flutter pub get 完成")


def flutter_pub_publish(*, dry_run: bool = False) -> None:
    print("📦 flutter pub publish --force ...")
    run_command(["flutter", "pub", "publish", "--force"], dry_run=dry_run)
    print("✅ 发布完成")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="box_pub_publish",
        description="自动升级 pubspec.yaml 版本号、更新 CHANGELOG、提交并发布 Flutter 包",
    )
    p.add_argument("--pubspec", default="pubspec.yaml", help="pubspec.yaml 路径（默认 ./pubspec.yaml）")
    p.add_argument("--changelog", default="CHANGELOG.md", help="CHANGELOG.md 路径（默认 ./CHANGELOG.md）")
    p.add_argument("--msg", nargs="+", required=True, help="更新说明内容（不需要引号，可多段）")

    p.add_argument("--no-pull", action="store_true", help="跳过 git pull")
    p.add_argument("--no-git", action="store_true", help="跳过 git add/commit/push")
    p.add_argument("--no-publish", action="store_true", help="跳过 flutter pub publish")
    p.add_argument("--skip-pub-get", action="store_true", help="跳过 flutter pub get")
    p.add_argument("--dry-run", action="store_true", help="预演：不改文件、不执行外部命令")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    pubspec_path = Path(args.pubspec)
    changelog_path = Path(args.changelog)
    msg_text = " ".join(args.msg).strip()

    if not msg_text:
        print("❌ --msg 不能为空")
        return 2

    try:
        # 若不是 git 仓库，自动降级：跳过 pull + git 操作
        git_ok = (not args.no_git) and is_git_repo(Path.cwd()) and (not args.dry_run)
        if not git_ok and not args.no_git:
            print("ℹ️ 当前目录不是 git 仓库或未安装 git，已自动跳过 git 操作（等同 --no-git）")

        if git_ok and (not args.no_pull):
            git_pull(dry_run=args.dry_run)

        if not pubspec_path.exists():
            print(f"❌ pubspec.yaml 不存在: {pubspec_path}")
            return 2

        project_name = extract_project_name(pubspec_path)

        new_version, old_version = update_pubspec_preserve_format(pubspec_path, dry_run=args.dry_run)
        update_changelog(changelog_path, new_version, msg_text, dry_run=args.dry_run)

        if not args.skip_pub_get:
            flutter_pub_get(dry_run=args.dry_run)

        if git_ok:
            git_commit(pubspec_path, changelog_path, project_name, new_version, dry_run=args.dry_run)
        else:
            print("ℹ️ 已跳过 git 操作（--no-git 或自动降级）")

        if not args.no_publish:
            flutter_pub_publish(dry_run=args.dry_run)
        else:
            print("ℹ️ 已跳过发布（--no-publish）")

        print(f"✅ 完成：{project_name} {old_version} → {new_version}")
        return 0

    except (CmdError, ValueError) as e:
        print(f"❌ {e}")
        return 1
    except KeyboardInterrupt:
        print("\n⚠️ 已取消")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

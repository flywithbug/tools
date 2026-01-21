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
    "summary": "自动升级 pubspec.yaml 版本号，更新 CHANGELOG.md，执行 flutter pub get，发布前检查（可交互处理 warning/info），提交并发布（支持 release 分支规则）",
    "usage": [
        "box_pub_publish --msg fix crash on iOS",
        "box_pub_publish --msg feat add new api --no-publish",
        "box_pub_publish --pubspec path/to/pubspec.yaml --changelog path/to/CHANGELOG.md --msg release notes",
        "box_pub_publish --msg hotfix --dry-run",
        "box_pub_publish --msg release notes --yes-warnings",
    ],
    "options": [
        {"flag": "--pubspec", "desc": "pubspec.yaml 路径（默认 ./pubspec.yaml）"},
        {"flag": "--changelog", "desc": "CHANGELOG.md 路径（默认 ./CHANGELOG.md）"},
        {"flag": "--msg", "desc": "更新说明（必填；可写多段，不需要引号）"},
        {"flag": "--no-pull", "desc": "跳过 git pull"},
        {"flag": "--no-git", "desc": "跳过 git add/commit/push（若不是 git 仓库也会自动跳过）"},
        {"flag": "--no-publish", "desc": "跳过 flutter pub publish"},
        {"flag": "--skip-pub-get", "desc": "跳过 flutter pub get"},
        {"flag": "--skip-checks", "desc": "跳过发布前检查（flutter analyze + git 变更白名单）"},
        {"flag": "--yes-warnings", "desc": "发布检查出现 issue（info/warning）时仍继续提交并发布（非交互/CI 推荐）"},
        {"flag": "--dry-run", "desc": "仅打印将执行的操作，不改文件、不跑命令"},
    ],
    "examples": [
        {"cmd": "box_pub_publish --msg fix null error", "desc": "拉代码→升级版本→更新 changelog→pub get→检查(可交互)→提交→发布"},
        {"cmd": "box_pub_publish --msg release notes --no-publish", "desc": "只提交不发布"},
        {"cmd": "box_pub_publish --msg release notes --yes-warnings", "desc": "检查有 issue 也自动继续提交并发布（适合 CI）"},
        {"cmd": "box_pub_publish --msg try --dry-run", "desc": "预演一次，不做任何修改"},
    ],
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


def run_command(
        cmd: list[str],
        *,
        dry_run: bool = False,
        cwd: Path | None = None,
        fail_on_warning: bool = False,
        warning_regex: str | re.Pattern[str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """运行外部命令；失败则抛异常（携带 stdout/stderr）。"""
    if dry_run:
        print("🧪 DRY-RUN:", " ".join(cmd))
        return None

    warn_pat: re.Pattern[str] | None = None
    if fail_on_warning:
        if warning_regex is None:
            warn_pat = re.compile(r"(?im)^\s*warning\s*[:\-]")
        elif isinstance(warning_regex, str):
            warn_pat = re.compile(warning_regex)
        else:
            warn_pat = warning_regex

    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    combined = (p.stdout or "") + "\n" + (p.stderr or "")

    if p.returncode != 0:
        msg = (
            f"执行命令失败: {' '.join(cmd)}\n"
            f"exit code: {p.returncode}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}\n"
        )
        raise CmdError(msg)

    if warn_pat and warn_pat.search(combined):
        msg = (
            f"命令输出包含 warning，已按失败处理: {' '.join(cmd)}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}\n"
        )
        raise CmdError(msg)

    return p


def git_pull(*, dry_run: bool = False) -> None:
    print("🔄 git pull ...")
    run_command(["git", "pull"], dry_run=dry_run)
    print("✅ 代码已更新")


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
    """版本升级策略：release 分支对齐分支版本，否则 patch+1（保留 build）。"""
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


def _git_porcelain_changed_paths(*, dry_run: bool = False) -> list[str]:
    """返回 git 工作区发生变更的路径列表（包含 staged/unstaged/untracked）。"""
    if dry_run:
        print("🧪 DRY-RUN: git status --porcelain")
        return []

    p = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    out = (p.stdout or "").splitlines()

    paths: list[str] = []
    for line in out:
        if len(line) < 4:
            continue
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1].strip()
        if path_part.startswith('"') and path_part.endswith('"'):
            path_part = path_part[1:-1]
        paths.append(path_part)
    return paths


def git_status_only_allowed_changes(
        *,
        allowed_exact: set[str],
        allowed_patterns: list[re.Pattern[str]],
        dry_run: bool = False,
) -> tuple[bool, list[str]]:
    changed = _git_porcelain_changed_paths(dry_run=dry_run)

    not_allowed: list[str] = []
    for p in changed:
        if p in allowed_exact:
            continue
        if any(pat.search(p) for pat in allowed_patterns):
            continue
        not_allowed.append(p)

    return (len(not_allowed) == 0), not_allowed


def extract_analyze_issue_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in (text or "").splitlines():
        if re.match(r"^\s*(info|warning|error)\s*•", line):
            lines.append(line.rstrip())
    return lines


def extract_analyze_error_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in (text or "").splitlines():
        if re.match(r"^\s*error\s*•", line):
            lines.append(line.rstrip())
    return lines


def confirm_continue_on_warnings(warnings: list[str], *, yes_warnings: bool) -> bool:
    """有 issue（info/warning）时，最后提示是否继续提交+发布（error 会更早退出）。"""
    if not warnings:
        return True

    print("\n⚠️ 发布检查发现 issue（info/warning）：")
    max_show = 80
    for i, w in enumerate(warnings[:max_show], 1):
        print(f"  {i}. {w}")
    if len(warnings) > max_show:
        print(f"  ...（共 {len(warnings)} 条，仅展示前 {max_show} 条）")

    if yes_warnings:
        print("ℹ️ 已指定 --yes-warnings：遇到 issue 仍继续提交并发布")
        return True

    if not sys.stdin.isatty():
        print("❌ 当前为非交互环境，且未指定 --yes-warnings，已中止提交与发布。")
        print("   如需继续，请加 --yes-warnings")
        return False

    ans = input("\n是否继续【提交 + 发布】？[y/N] ").strip().lower()
    return ans in ("y", "yes")


def flutter_pub_get(*, dry_run: bool = False) -> None:
    print("🧩 flutter pub get ...")
    run_command(["flutter", "pub", "get"], dry_run=dry_run)
    print("✅ flutter pub get 完成")


def flutter_analyze(*, dry_run: bool = False) -> list[str]:
    """flutter analyze：有 error 直接退出；只有 info/warning 则汇总后提示是否继续。"""
    print("🔎 flutter analyze ...")

    if dry_run:
        print("✅ flutter analyze（dry-run）")
        return []

    p = subprocess.run(["flutter", "analyze"], capture_output=True, text=True)
    combined = (p.stdout or "") + "\n" + (p.stderr or "")

    error_lines = extract_analyze_error_lines(combined)
    issue_lines = extract_analyze_issue_lines(combined)

    if error_lines:
        msg = (
                "flutter analyze 发现 error，已中止：\n"
                + "\n".join(f"- {e}" for e in error_lines[:200])
                + ("\n...(error 过多，已截断)" if len(error_lines) > 200 else "")
                + "\n\nstdout:\n"
                + (p.stdout or "")
                + "\n\nstderr:\n"
                + (p.stderr or "")
        )
        raise CmdError(msg)

    if issue_lines:
        print(f"⚠️ flutter analyze 发现 {len(issue_lines)} 条 issue（无 error）")
        return issue_lines

    print("✅ flutter analyze 通过（无 issue）")
    return []


def pre_publish_checks(
        *,
        dry_run: bool = False,
        yes_warnings: bool = False,
        pubspec_path: Path,
        changelog_path: Path,
) -> bool:
    """发布前检查：检查 analyze + git 变更白名单。"""
    print("🧰 发布前检查 ...")

    if is_git_repo(Path.cwd()):
        # 关键：git status 输出相对 repo 根目录；所以允许列表也必须相对 repo 根目录
        # 但你希望提交阶段用 `git add .`，因此这里只做“变更是否符合规则”的校验即可。
        repo_root_p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        repo_root = Path(repo_root_p).resolve()

        pubspec_rel = pubspec_path.resolve().relative_to(repo_root).as_posix()
        changelog_rel = changelog_path.resolve().relative_to(repo_root).as_posix()

        allowed_exact = {pubspec_rel, changelog_rel}

        # 允许：当前包目录内任意 pubspec.lock（包含 example/pubspec.lock）
        pkg_dir = Path(pubspec_rel).parent.as_posix()
        if pkg_dir == ".":
            pkg_dir = ""
        if pkg_dir:
            lock_pat = re.compile(rf"^{re.escape(pkg_dir)}/.*pubspec\.lock$")
        else:
            lock_pat = re.compile(r"(^|/)\bpubspec\.lock$")

        allowed_patterns = [lock_pat]

        ok, not_allowed = git_status_only_allowed_changes(
            allowed_exact=allowed_exact,
            allowed_patterns=allowed_patterns,
            dry_run=dry_run,
        )
        if not ok:
            raise CmdError(
                "发布前检查失败：git 工作区存在非预期变更（不在允许列表内）：\n"
                + "\n".join(f"- {p}" for p in not_allowed)
                + "\n\n已允许：\n"
                + f"- {pubspec_rel}\n"
                + f"- {changelog_rel}\n"
                + f"- {(pkg_dir + '/' if pkg_dir else '')}**/pubspec.lock（仅当前包目录内）\n"
                + "\n请先提交/暂存/清理这些文件后再发布。"
            )
        print("✅ git 变更检查通过（变更均符合规则）")
    else:
        print("ℹ️ 当前目录不是 git 仓库，跳过 git 变更检查")

    issues = flutter_analyze(dry_run=dry_run)
    ok2 = confirm_continue_on_warnings(issues, yes_warnings=yes_warnings)

    if ok2:
        print("✅ 发布前检查通过（选择继续）")
    else:
        print("⛔ 已选择中止：不会执行 git commit/push，也不会 publish")
    return ok2


def git_commit_all(project_name: str, new_version: str, *, dry_run: bool = False) -> None:
    """
    按你的新规则：提交阶段不挑文件，直接 `git add .`，
    前面 pre_publish_checks 已保证变更只包含允许范围。
    """
    msg = f"build: {project_name} + {new_version}"

    print("📝 git add . ...")
    run_command(["git", "add", "."], dry_run=dry_run)

    # 没有变更则不要 commit（避免 exit 1）
    if not dry_run:
        p = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True)
        staged = (p.stdout or "").strip()
        if not staged:
            print("ℹ️ 暂存区无变更，跳过 git commit/push")
            return

    print("📝 git commit ...")
    run_command(["git", "commit", "-m", msg], dry_run=dry_run)

    print("🚀 git push ...")
    run_command(["git", "push"], dry_run=dry_run)

    print(f"✅ 已提交并推送: {msg}")


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
    p.add_argument("--skip-checks", action="store_true", help="跳过发布前检查（flutter analyze + git 变更白名单）")
    p.add_argument("--yes-warnings", action="store_true", help="发布检查出现 issue（info/warning）时仍继续提交并发布（非交互/CI 推荐）")
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

        should_continue = True
        if (not args.no_publish) and (not args.skip_checks):
            should_continue = pre_publish_checks(
                dry_run=args.dry_run,
                yes_warnings=args.yes_warnings,
                pubspec_path=pubspec_path,
                changelog_path=changelog_path,
            )
        elif not args.no_publish:
            print("ℹ️ 已跳过发布前检查（--skip-checks）")

        if not should_continue:
            print(f"✅ 已结束：{project_name} {old_version} → {new_version}（已更新文件，但未提交/未发布）")
            return 0

        if git_ok:
            git_commit_all(project_name, new_version, dry_run=args.dry_run)
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

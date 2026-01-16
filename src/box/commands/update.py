from __future__ import annotations

import os
import shutil
import subprocess
import sys


PKG_NAME = "flywithbug-box"  # pyproject 的 project.name


def _run(cmd: list[str], verbose: bool = False) -> int:
    if verbose:
        print("run:", " ".join(cmd))
    p = subprocess.run(cmd)
    return int(p.returncode)


def cmd_update(args) -> int:
    verbose = bool(getattr(args, "verbose", False))
    print("== box update ==")

    pipx = shutil.which("pipx")
    if pipx:
        # pipx upgrade 对 git 安装/本地安装有时行为不同；upgrade 不行就 fallback reinstall
        rc = _run([pipx, "upgrade", PKG_NAME], verbose=verbose)
        if rc == 0:
            print("update: OK (pipx upgrade)")
            return 0

        print("update: pipx upgrade failed, trying reinstall...")
        rc = _run([pipx, "reinstall", PKG_NAME], verbose=verbose)
        if rc == 0:
            print("update: OK (pipx reinstall)")
            return 0

        print("update: FAILED (pipx).")
        return 1

    print("pipx not found.")
    print("建议：重新运行 install.sh 安装/修复 pipx，然后再执行 box update。")
    # 也给一个保底路径：如果用户是 pip --user 装的
    if shutil.which("python3"):
        print("保底尝试：python -m pip install --user --upgrade flywithbug-box（不推荐，但可救急）")
    return 2

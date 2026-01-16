from __future__ import annotations

import os
import shutil
from pathlib import Path


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        cur_dst = dst / rel
        cur_dst.mkdir(parents=True, exist_ok=True)
        for fn in filenames:
            s = Path(dirpath) / fn
            d = cur_dst / fn
            _copy_file(s, d)


def _collect_all_files(root: Path) -> set[Path]:
    out: set[Path] = set()
    if root.is_file():
        out.add(Path("."))
        return out
    for dirpath, _, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        for fn in filenames:
            out.add(rel_dir / fn)
    return out


def cmd_sync(args) -> int:
    src = Path(args.src).expanduser().resolve()
    dst = Path(args.dst).expanduser().resolve()
    dry = bool(args.dry_run)
    do_delete = bool(args.delete)
    yes = bool(args.yes)

    print("== box sync ==")
    print(f"src: {src}")
    print(f"dst: {dst}")
    print(f"dry-run: {dry}")
    print(f"delete: {do_delete}")

    if not src.exists():
        print("sync: src not found")
        return 2

    if do_delete and not yes:
        print("sync: --delete 是危险操作，需要同时提供 --yes 才会执行。")
        return 3

    # 复制
    actions = []
    if src.is_file():
        target = dst
        if dst.is_dir():
            target = dst / src.name
        actions.append(("COPY", src, target))
    else:
        # dst 当作目录根
        actions.append(("COPY_TREE", src, dst))

    # delete 计算
    delete_list: list[Path] = []
    if do_delete:
        src_files = _collect_all_files(src)
        if src.is_file():
            # file sync 时不做 delete
            src_files = {Path(src.name)}
        dst_files = _collect_all_files(dst) if dst.exists() else set()
        extra = sorted(dst_files - src_files, key=lambda p: str(p))
        delete_list = [dst / p for p in extra]

    if dry:
        for a in actions:
            print(f"{a[0]}: {a[1]} -> {a[2]}")
        if delete_list:
            print("DELETE:")
            for p in delete_list[:200]:
                print("  -", p)
            if len(delete_list) > 200:
                print(f"  ... ({len(delete_list)-200} more)")
        print("sync: dry-run done.")
        return 0

    # 执行 copy
    for a in actions:
        if a[0] == "COPY":
            _copy_file(a[1], a[2])
        else:
            dst.mkdir(parents=True, exist_ok=True)
            _copy_tree(a[1], a[2])

    # 执行 delete
    if delete_list:
        for p in delete_list:
            try:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
            except Exception as e:
                print(f"warn: failed to delete {p}: {e}")

    print("sync: OK")
    return 0

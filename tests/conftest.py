import sys
from pathlib import Path

import pytest


# Ensure 'src/' is on sys.path so 'box_tools' can be imported when running tests from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    """切到临时目录执行（避免污染仓库）。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path

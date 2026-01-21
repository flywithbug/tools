import os
from pathlib import Path

import pytest


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    """切到临时目录执行（避免污染仓库）。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_pubspec(path: Path, version: str = "1.2.3+2026012100", extra: str = ""):
    text = f"""name: demo
description: demo

version: {version}

environment:
  sdk: ">=3.0.0 <4.0.0"

dependencies:
{extra}
"""
    path.write_text(text, encoding="utf-8")
    return path

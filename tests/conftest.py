import pytest


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    """切到临时目录执行（避免污染仓库）。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path

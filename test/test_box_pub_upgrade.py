from pathlib import Path

import pytest

from box_tools.flutter.pub_version import cli as pub_version_cli


def test_patch_bump_no_git(chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text("name: demo\nversion: 1.2.3+abc\n", encoding="utf-8")

    rc = pub_version_cli.main(["patch", "--no-git", "--file", str(pubspec)])
    assert rc == 0

    text = pubspec.read_text(encoding="utf-8")
    assert "version: 1.2.4+abc" in text


def test_minor_bump_no_git(chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text("name: demo\nversion: 1.2.3\n", encoding="utf-8")

    rc = pub_version_cli.main(["minor", "--no-git", "--file", str(pubspec)])
    assert rc == 0

    text = pubspec.read_text(encoding="utf-8")
    assert "version: 1.3.0" in text


def test_interactive_choose_patch(monkeypatch, chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text("name: demo\nversion: 1.2.3\n", encoding="utf-8")

    # 选择 "2" => patch
    monkeypatch.setattr("builtins.input", lambda _: "2")

    rc = pub_version_cli.main(["--no-git", "--file", str(pubspec)])
    assert rc == 0
    assert "version: 1.2.4" in pubspec.read_text(encoding="utf-8")


def test_interactive_quit(monkeypatch, chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text("name: demo\nversion: 1.2.3\n", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "q")

    rc = pub_version_cli.main(["--no-git", "--file", str(pubspec)])
    assert rc == 0  # 正常退出
    assert "version: 1.2.3" in pubspec.read_text(encoding="utf-8")


def test_invalid_version_format(chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text("name: demo\nversion: hello\n", encoding="utf-8")

    rc = pub_version_cli.main(["patch", "--no-git", "--file", str(pubspec)])
    assert rc == 2

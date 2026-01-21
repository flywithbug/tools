from pathlib import Path

from box_tools.flutter.pub_version import tool as pub_version_tool


def test_patch_bump_no_git(chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text("name: demo\nversion: 1.2.3+abc\n", encoding="utf-8")

    rc = pub_version_tool.main(["patch", "--no-git", "--file", str(pubspec)])
    assert rc == 0

    text = pubspec.read_text(encoding="utf-8")
    assert "version: 1.2.4+abc" in text


def test_minor_bump_no_git(chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text("name: demo\nversion: 1.2.3\n", encoding="utf-8")

    rc = pub_version_tool.main(["minor", "--no-git", "--file", str(pubspec)])
    assert rc == 0

    text = pubspec.read_text(encoding="utf-8")
    assert "version: 1.3.0" in text

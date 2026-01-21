from pathlib import Path

from box_tools.flutter.pub_version import tool as tool


def test_patch_bump_no_git(chdir_tmp):
    pubspec = Path('pubspec.yaml')
    pubspec.write_text('name: demo\nversion: 1.2.3+abc\n', encoding='utf-8')
    rc = tool.main(['patch', '--no-git', '--file', str(pubspec)])
    assert rc == 0
    assert 'version: 1.2.4+abc' in pubspec.read_text(encoding='utf-8')

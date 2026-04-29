import importlib


def test_smoke_import():
    mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    assert hasattr(mod, 'main')


def test_publish_success_echoes_released_version(tmp_path, monkeypatch):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    publish_mod = importlib.import_module('box_tools.flutter.pubspec.pub_publish')

    pubspec = tmp_path / 'pubspec.yaml'
    changelog = tmp_path / 'CHANGELOG.md'
    pubspec.write_text('name: demo_pkg\nversion: 1.2.3\n', encoding='utf-8')
    changelog.write_text('# Changelog\n\n', encoding='utf-8')

    logs = []
    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=pubspec,
        outdated_json_path=None,
        dry_run=False,
        yes=True,
        interactive=False,
        echo=logs.append,
        confirm=lambda _: True,
    )

    monkeypatch.setattr(publish_mod, '_git_check_repo', lambda _: None)
    monkeypatch.setattr(publish_mod, '_git_is_dirty', lambda _: False)
    monkeypatch.setattr(publish_mod, '_git_pull_ff_only', lambda _: None)
    monkeypatch.setattr(publish_mod, '_git_current_branch', lambda _: 'main')
    monkeypatch.setattr(publish_mod, 'flutter_pub_get', lambda _: None)
    monkeypatch.setattr(publish_mod, 'flutter_analyze_gate', lambda _: None)
    monkeypatch.setattr(
        publish_mod,
        '_git_add_commit_push',
        lambda _, *, new_version, old_version, note: None,
    )
    monkeypatch.setattr(
        publish_mod,
        'flutter_pub_publish',
        lambda _, *, dry_run: None,
    )

    rc = publish_mod.publish(ctx)

    assert rc == 0
    assert any('当前发布成功版本：demo_pkg 1.2.4' in line for line in logs)


def test_upgrade_plan_does_not_exceed_current_minor_version(tmp_path, monkeypatch):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    upgrade_mod = importlib.import_module('box_tools.flutter.pubspec.pub_upgrade')

    pubspec = tmp_path / 'pubspec.yaml'
    pubspec.write_text(
        '\n'.join([
            'name: demo_pkg',
            'version: 3.54.1',
            'dependencies:',
            '  ap_api:',
            '    hosted:',
            '      url: https://example.com',
            '      name: ap_api',
            '    version: ^3.50.0',
            '',
        ]),
        encoding='utf-8',
    )

    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=pubspec,
        outdated_json_path=None,
        dry_run=False,
        yes=True,
        interactive=False,
        echo=lambda _: None,
        confirm=lambda _: True,
    )

    monkeypatch.setattr(
        upgrade_mod,
        'flutter_pub_outdated_show_all_json',
        lambda _: {
            'packages': [
                {
                    'package': 'ap_api',
                    'current': {'version': '3.50.0'},
                    'upgradable': {'version': '3.54.7'},
                    'resolvable': {'version': '3.54.9'},
                    'latest': {'version': '3.55.2'},
                }
            ]
        },
    )

    privates = upgrade_mod.read_pubspec_private_dependencies(pubspec.read_text(encoding='utf-8'))
    plan = upgrade_mod.build_private_upgrade_plan_from_pubspec(ctx, privates)

    assert len(plan) == 1
    assert plan[0].name == 'ap_api'
    assert plan[0].target == '3.54.9'


def test_upgrade_plan_skips_when_all_candidates_exceed_current_minor_version(tmp_path, monkeypatch):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    upgrade_mod = importlib.import_module('box_tools.flutter.pubspec.pub_upgrade')

    pubspec = tmp_path / 'pubspec.yaml'
    pubspec.write_text(
        '\n'.join([
            'name: demo_pkg',
            'version: 3.54.1',
            'dependencies:',
            '  ap_api:',
            '    hosted:',
            '      url: https://example.com',
            '      name: ap_api',
            '    version: ^3.54.0',
            '',
        ]),
        encoding='utf-8',
    )

    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=pubspec,
        outdated_json_path=None,
        dry_run=False,
        yes=True,
        interactive=False,
        echo=lambda _: None,
        confirm=lambda _: True,
    )

    monkeypatch.setattr(
        upgrade_mod,
        'flutter_pub_outdated_show_all_json',
        lambda _: {
            'packages': [
                {
                    'package': 'ap_api',
                    'current': {'version': '3.54.0'},
                    'upgradable': {'version': '3.55.0'},
                    'resolvable': {'version': '3.55.1'},
                    'latest': {'version': '3.56.0'},
                }
            ]
        },
    )

    privates = upgrade_mod.read_pubspec_private_dependencies(pubspec.read_text(encoding='utf-8'))
    plan = upgrade_mod.build_private_upgrade_plan_from_pubspec(ctx, privates)

    assert plan == []

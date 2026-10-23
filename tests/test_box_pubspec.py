import importlib

import pytest


@pytest.fixture(autouse=True)
def isolate_pubspec_state(tmp_path, monkeypatch):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    monkeypatch.setattr(tool_mod, 'APP_INTEGRATE_STATE_PATH', tmp_path / 'pubspec_state.json')


def test_doctor_does_not_require_cloudsmith_api_key(tmp_path, monkeypatch):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    doctor_mod = importlib.import_module('box_tools.flutter.pubspec.doctor')
    (tmp_path / 'pubspec.yaml').write_text(
        'name: demo\nversion: 1.2.3\npublish_to: none\n', encoding='utf-8',
    )
    ctx = tool_mod._mk_ctx(tool_mod.build_parser().parse_args([
        'doctor', '--project-root', str(tmp_path),
    ]))
    monkeypatch.delenv('cloudsmithApiKey', raising=False)
    monkeypatch.setattr(doctor_mod, 'run_cmd', lambda *a, **kw: tool_mod.CmdResult(0, 'Flutter OK', ''))

    ok, _, errors = doctor_mod.collect(ctx)

    assert ok
    assert errors == []


@pytest.mark.parametrize(('cmd', 'code', 'out', 'err', 'expect_hint'), [
    (['flutter', 'pub', 'get'], 1, '', 'Authentication failed: https://dart.cloudsmith.io/apex-dao-llc/app/', True),
    (['dart', 'pub', 'get', '--offline'], 1, 'Package unavailable at dart.cloudsmith.io', '', True),
    (['flutter', 'pub', 'outdated', '--json'], 1, 'dart.cloudsmith.io unavailable', 'Version solving failed', True),
    (['flutter', 'pub', 'get'], 0, 'Downloaded from dart.cloudsmith.io', '', False),
    (['flutter', 'pub', 'get'], 1, '', 'Version solving failed for pub.dev', False),
    (['git', 'pull'], 1, '', 'cloudsmith unavailable', False),
])
def test_pub_cloudsmith_failure_suggests_token_command(
    tmp_path, monkeypatch, cmd, code, out, err, expect_hint,
):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    completed = tool_mod.subprocess.CompletedProcess(cmd, code, out, err)
    monkeypatch.setattr(tool_mod.subprocess, 'run', lambda *a, **kw: completed)

    result = tool_mod.run_cmd(cmd, cwd=tmp_path)

    hint = 'dart pub token add https://dart.cloudsmith.io/apex-dao-llc/app'
    assert (hint in result.err) is expect_hint
    assert result.code == code
    assert result.out == out
    assert err in result.err
    if expect_hint:
        assert out in result.err
    else:
        assert result.err == err


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
        execute_publish=True,
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


def test_publish_flag_defaults_to_false_and_accepts_true():
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')

    default_args = tool_mod.build_parser().parse_args(['publish'])
    enabled_args = tool_mod.build_parser().parse_args(['publish', '-p', 'true'])

    assert tool_mod._mk_ctx(default_args).execute_publish is False
    assert tool_mod._mk_ctx(enabled_args).execute_publish is True


def test_publish_app_options_generate_cloud_integration_trailers(tmp_path, monkeypatch):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    publish_mod = importlib.import_module('box_tools.flutter.pubspec.pub_publish')

    pubspec = tmp_path / 'pubspec.yaml'
    changelog = tmp_path / 'CHANGELOG.md'
    pubspec.write_text('name: demo_pkg\nversion: 1.2.3\n', encoding='utf-8')
    changelog.write_text('# Changelog\n\n', encoding='utf-8')

    args = tool_mod.build_parser().parse_args([
        'publish', '--project-root', str(tmp_path), '--app-integrate',
        'release-3.63.0', '--app-package', 'gray',
    ])
    ctx = tool_mod._mk_ctx(args)
    assert ctx.app_integrate_branch == 'release-3.63.0'
    assert ctx.app_package == 'gray'

    commands = []

    def fake_run(cmd, cwd, capture=True):
        commands.append(cmd)
        return tool_mod.CmdResult(code=0, out='', err='')

    monkeypatch.setattr(publish_mod, 'run_cmd', fake_run)
    monkeypatch.setattr(publish_mod, '_git_current_branch', lambda _: 'main')
    monkeypatch.setattr(publish_mod, '_git_has_remote_branch', lambda *_: False)

    publish_mod._git_add_commit_push(
        ctx,
        new_version='1.2.4',
        old_version='1.2.3',
        note='release note',
    )

    commit_cmd = next(cmd for cmd in commands if cmd[:2] == ['git', 'commit'])
    message = commit_cmd[-1]
    assert 'App-Integrate: release-3.63.0' in message
    assert 'App-Package: gray' in message
    assert 'App-Platform: both' in message
    assert '[skip ci]' not in message


def test_publish_app_package_requires_app_integrate():
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    args = tool_mod.build_parser().parse_args(['publish', '--app-package', 'qa'])

    try:
        tool_mod._mk_ctx(args)
    except ValueError as error:
        assert '--app-package 必须与 --app-integrate 一起使用' in str(error)
    else:
        raise AssertionError('expected app package validation error')


def test_publish_app_integrate_accepts_latest_release_branch():
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    args = tool_mod.build_parser().parse_args(['publish', '--app-integrate', 'latest'])

    assert tool_mod._mk_ctx(args).app_integrate_branch == 'latest'


def test_menu_publish_app_adds_latest_integration_trailer_option(monkeypatch, tmp_path):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    monkeypatch.setattr(tool_mod, '_current_release_branch', lambda _root: None)
    argv = []
    answers = iter(['3', ''])
    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=tmp_path / 'pubspec.yaml',
        outdated_json_path=None,
        dry_run=False,
        yes=False,
        interactive=True,
        echo=lambda _: None,
        confirm=lambda _: True,
    )

    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(tool_mod, 'main', lambda command: argv.extend(command) or 0)

    assert tool_mod.run_menu(ctx) == 0
    assert argv[1] == 'publish'
    assert argv[-2:] == ['--app-integrate', 'latest']


def test_menu_publish_app_defaults_to_latest_without_local_cache(monkeypatch, tmp_path):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    argv = []
    answers = iter(['3', ''])
    monkeypatch.setattr(tool_mod, 'APP_INTEGRATE_STATE_PATH', tmp_path / 'box_pubspec_state.json')
    monkeypatch.setattr(tool_mod, '_current_release_branch', lambda _root: None)
    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=tmp_path / 'pubspec.yaml',
        outdated_json_path=None,
        dry_run=False,
        yes=False,
        interactive=True,
        echo=lambda _: None,
        confirm=lambda _: True,
    )

    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(tool_mod, 'main', lambda command: argv.extend(command) or 0)

    assert tool_mod.run_menu(ctx) == 0
    assert argv[-2:] == ['--app-integrate', 'latest']


def test_menu_publish_app_uses_and_updates_cached_release_branch(monkeypatch, tmp_path):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    state_path = tmp_path / 'box_pubspec_state.json'
    monkeypatch.setattr(tool_mod, 'APP_INTEGRATE_STATE_PATH', state_path)
    monkeypatch.setattr(tool_mod, '_current_release_branch', lambda _root: None)

    argv = []
    first_answers = iter(['3', 'release-3.63.0'])
    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=tmp_path / 'pubspec.yaml',
        outdated_json_path=None,
        dry_run=False,
        yes=False,
        interactive=True,
        echo=lambda _: None,
        confirm=lambda _: True,
    )
    monkeypatch.setattr('builtins.input', lambda _: next(first_answers))
    monkeypatch.setattr(tool_mod, 'main', lambda command: argv.extend(command) or 0)
    assert tool_mod.run_menu(ctx) == 0
    assert argv[-2:] == ['--app-integrate', 'release-3.63.0']

    argv.clear()
    second_answers = iter(['3', ''])
    monkeypatch.setattr('builtins.input', lambda _: next(second_answers))
    assert tool_mod.run_menu(ctx) == 0
    assert argv[-2:] == ['--app-integrate', 'release-3.63.0']

    argv.clear()
    third_answers = iter(['3', 'latest'])
    monkeypatch.setattr('builtins.input', lambda _: next(third_answers))
    assert tool_mod.run_menu(ctx) == 0
    assert argv[-2:] == ['--app-integrate', 'latest']


def test_menu_publish_app_prefers_current_release_branch(monkeypatch, tmp_path):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    state_path = tmp_path / 'box_pubspec_state.json'
    monkeypatch.setattr(tool_mod, 'APP_INTEGRATE_STATE_PATH', state_path)
    # 当前分支是 release-3.63.0，且本机无缓存：回车应默认当前分支而非 latest
    monkeypatch.setattr(tool_mod, '_current_release_branch', lambda _root: 'release-3.63.0')

    argv = []
    answers = iter(['3', ''])
    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=tmp_path / 'pubspec.yaml',
        outdated_json_path=None,
        dry_run=False,
        yes=False,
        interactive=True,
        echo=lambda _: None,
        confirm=lambda _: True,
    )
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(tool_mod, 'main', lambda command: argv.extend(command) or 0)

    assert tool_mod.run_menu(ctx) == 0
    assert argv[-2:] == ['--app-integrate', 'release-3.63.0']


def test_menu_publish_app_current_branch_overrides_cache(monkeypatch, tmp_path):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    state_path = tmp_path / 'box_pubspec_state.json'
    state_path.write_text('{"app_integrate_branch": "release-3.60.0"}\n', encoding='utf-8')
    monkeypatch.setattr(tool_mod, 'APP_INTEGRATE_STATE_PATH', state_path)
    # 当前分支优先于缓存：缓存是 3.60.0，但当前在 3.63.0 → 回车用 3.63.0
    monkeypatch.setattr(tool_mod, '_current_release_branch', lambda _root: 'release-3.63.0')

    argv = []
    answers = iter(['3', ''])
    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=tmp_path / 'pubspec.yaml',
        outdated_json_path=None,
        dry_run=False,
        yes=False,
        interactive=True,
        echo=lambda _: None,
        confirm=lambda _: True,
    )
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(tool_mod, 'main', lambda command: argv.extend(command) or 0)

    assert tool_mod.run_menu(ctx) == 0
    assert argv[-2:] == ['--app-integrate', 'release-3.63.0']


def test_menu_publish_app_package_adds_selected_package_option(monkeypatch, tmp_path):
    tool_mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    argv = []
    answers = iter(['4', 'release-3.63.0', '2', '3'])
    ctx = tool_mod.Context(
        project_root=tmp_path,
        pubspec_path=tmp_path / 'pubspec.yaml',
        outdated_json_path=None,
        dry_run=False,
        yes=False,
        interactive=True,
        echo=lambda _: None,
        confirm=lambda _: True,
    )

    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(tool_mod, 'main', lambda command: argv.extend(command) or 0)

    assert tool_mod.run_menu(ctx) == 0
    assert argv[1] == 'publish'
    assert argv[-6:] == ['--app-integrate', 'release-3.63.0', '--app-platform', 'both', '--app-package', 'gray']


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




@pytest.mark.parametrize('environment', ['none', 'qa', 'gray', 'prod', 'store', 'reserved', 'operations'])
def test_app_integration_uses_cloud_publish(environment):
    from box_tools.flutter.pubspec import tool
    args = ['publish', '--app-integrate', 'latest', '--app-package', environment, '--app-platform', 'ios']
    assert not tool._mk_ctx(tool.build_parser().parse_args(args)).execute_publish
    with pytest.raises(ValueError, match='不能配合 -p true'):
        tool._mk_ctx(tool.build_parser().parse_args(args + ['-p', 'true']))


@pytest.mark.parametrize('preview', [True, False])
def test_app_publish_only_pushes_request_and_never_runs_local_publication(tmp_path, monkeypatch, preview):
    from box_tools.flutter.pubspec import pub_publish, tool
    pubspec = tmp_path / 'pubspec.yaml'
    pubspec.write_text('name: ap_demo\nversion: 3.69.1\n')
    changelog = tmp_path / 'CHANGELOG.md'
    changelog.write_text('# Changelog\n')
    logs, calls = [], []
    ctx = tool.Context(tmp_path, pubspec, None, preview, True, False,
        logs.append, lambda _: True, app_integrate_branch='release-3.69.0', app_package='qa', app_platform='ios')
    monkeypatch.setattr(pub_publish, '_git_check_repo', lambda _: None)
    monkeypatch.setattr(pub_publish, '_git_is_dirty', lambda _: False)
    monkeypatch.setattr(pub_publish, '_git_current_branch', lambda _: 'release-3.69.0')
    monkeypatch.setattr(pub_publish, '_git_has_remote_branch', lambda *a: True)
    monkeypatch.setattr(pub_publish, '_git_pull_ff_only', lambda _: calls.append('pull'))
    monkeypatch.setattr(pub_publish, 'flutter_pub_get', lambda _: calls.append('get'))
    monkeypatch.setattr(pub_publish, 'flutter_analyze_gate', lambda _: calls.append('analyze'))
    monkeypatch.setattr(pub_publish, '_git_add_commit_push', lambda *a, **kw: calls.append('push'))
    monkeypatch.setattr(pub_publish, 'flutter_pub_publish', lambda *a, **kw: pytest.fail('must publish through workflow'))
    assert pub_publish.publish(ctx) == 0
    if preview:
        assert calls == []
        assert pubspec.read_text() == 'name: ap_demo\nversion: 3.69.1\n'
        assert changelog.read_text() == '# Changelog\n'
    else:
        assert calls == ['pull', 'get', 'analyze', 'push']
        assert any('GitHub Actions 将先发布组件' in line for line in logs)


def test_app_request_rejects_unpublished_component_branch(tmp_path, monkeypatch):
    from box_tools.flutter.pubspec import pub_publish, tool
    ctx = tool._mk_ctx(tool.build_parser().parse_args(['publish', '--app-integrate', 'latest']))
    monkeypatch.setattr(pub_publish, '_git_current_branch', lambda _: 'main')
    with pytest.raises(RuntimeError, match='release'):
        pub_publish.publish(ctx)

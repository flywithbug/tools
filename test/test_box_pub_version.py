import json
from pathlib import Path

import pytest

from box_tools.flutter.pub_upgrade import cli as pub_upgrade_cli


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_upgrade_private_hosted_respects_upper_bound(monkeypatch, chdir_tmp):
    # pubspec.yaml：项目版本 3.45.1 => upper bound < 3.46.0
    pubspec = Path("pubspec.yaml")
    pubspec.write_text(
        """name: demo
version: 3.45.1+2026012100

dependencies:
  foo:
    hosted:
      name: foo
      url: "https://dart.cloudsmith.io/org/repo/"
    version: ^3.45.1
""",
        encoding="utf-8",
    )

    # mock subprocess.run/capture：让 flutter pub get 成功；outdated 返回 JSON
    def fake_run(cmd, capture_output=False, text=False, cwd=None, check=False):
        # pub get：returncode 0
        if cmd[:3] == ["flutter", "pub", "get"]:
            return FakeCompleted(0, "", "")
        # outdated：返回 packages 列表
        if cmd[:4] == ["flutter", "pub", "outdated", "--json"]:
            data = {
                "packages": [
                    {
                        "package": "foo",
                        "current": "3.45.1",
                        "upgradable": "3.45.2",
                        "resolvable": "3.45.8",
                        "latest": "3.46.1",  # 注意：latest 越界（>=3.46.0），应当被拒绝
                    }
                ]
            }
            return FakeCompleted(0, json.dumps(data), "")
        # git 相关：本测试用 --no-git，所以不应被调用；若被调用直接失败提醒
        if cmd and cmd[0] == "git":
            return FakeCompleted(1, "", "git should not be called")
        return FakeCompleted(0, "", "")

    # 你的实现里用 subprocess.run + capture_output 的组合：这里统一 patch
    monkeypatch.setattr(pub_upgrade_cli.subprocess, "run", fake_run)

    # 自动确认（不走 input）
    rc = pub_upgrade_cli.main(["--yes", "--no-git"])
    assert rc == 0

    # 期望：选择 resolvable=3.45.8（因为 latest 越界），并替换 version 约束
    text = pubspec.read_text(encoding="utf-8")
    assert "version: 3.45.8" in text or "version: ^3.45.8" in text or "version: 3.45.8" in text
    # 你的实现是把 constraint 直接替换成目标版本（不加 ^），所以通常断言：
    assert "version: 3.45.8" in text


def test_upgrade_skipped_package(monkeypatch, chdir_tmp):
    pubspec = Path("pubspec.yaml")
    pubspec.write_text(
        """name: demo
version: 1.0.0

dependencies:
  ap_recaptcha:
    hosted:
      name: ap_recaptcha
      url: https://dart.cloudsmith.io/org/repo/
    version: ^1.0.0
""",
        encoding="utf-8",
    )

    def fake_run(cmd, capture_output=False, text=False, cwd=None, check=False):
        if cmd[:3] == ["flutter", "pub", "get"]:
            return FakeCompleted(0, "", "")
        if cmd[:4] == ["flutter", "pub", "outdated", "--json"]:
            data = {
                "packages": [
                    {"package": "ap_recaptcha", "current": "1.0.0", "latest": "1.0.9"}
                ]
            }
            return FakeCompleted(0, json.dumps(data), "")
        return FakeCompleted(0, "", "")

    monkeypatch.setattr(pub_upgrade_cli.subprocess, "run", fake_run)

    rc = pub_upgrade_cli.main(["--yes", "--no-git"])
    assert rc == 0
    # 默认 skip ap_recaptcha，所以不应改动
    assert "version: ^1.0.0" in pubspec.read_text(encoding="utf-8")

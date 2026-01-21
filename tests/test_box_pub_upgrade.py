from box_tools.flutter.pub_upgrade import tool as pub_upgrade_tool


def test_smoke_import_only():
    # 只验证模块可导入、存在 main（避免依赖 flutter/git 环境）
    assert hasattr(pub_upgrade_tool, "main")

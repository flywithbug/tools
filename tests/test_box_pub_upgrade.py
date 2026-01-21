from box_tools.flutter.pub_upgrade import tool as tool


def test_smoke_import_only():
    assert hasattr(tool, 'main')

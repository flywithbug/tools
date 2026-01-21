import importlib


def test_smoke_import():
    mod = importlib.import_module('box_tools.flutter.pub_publish.tool')
    assert hasattr(mod, 'main')

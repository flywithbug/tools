import importlib


def test_smoke_import():
    mod = importlib.import_module('box_tools.flutter.pubspec.tool')
    assert hasattr(mod, 'main')

import importlib


def test_smoke_import():
    mod = importlib.import_module('box_tools.flutter.riverpod_gen.tool')
    assert hasattr(mod, 'main')

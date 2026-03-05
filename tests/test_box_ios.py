import importlib


def test_smoke_import():
    mod = importlib.import_module('box_tools.iOS.tools.tool')
    assert hasattr(mod, 'main')

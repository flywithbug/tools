import importlib


def test_smoke_import():
    mod = importlib.import_module('box_tools.ai.files.tool')
    assert hasattr(mod, 'main')

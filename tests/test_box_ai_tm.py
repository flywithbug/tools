import importlib


def test_smoke_import():
    mod = importlib.import_module('ai_tm.tool')
    assert hasattr(mod, 'main')

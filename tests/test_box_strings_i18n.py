import importlib


def test_smoke_import():
    mod = importlib.import_module('box_tools.iOS.strings_i18n.tool')
    assert hasattr(mod, 'main')

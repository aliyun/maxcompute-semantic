"""Smoke test that the osi/ package imports and exposes its public surface."""

import pytest


def test_osi_module_importable():
    import maxcompute_semantic.osi as osi

    assert hasattr(osi, "OSI_SCHEMA_VERSION")
    assert hasattr(osi, "to_osi_dict")
    assert hasattr(osi, "dump_yaml")
    assert osi.OSI_SCHEMA_VERSION == "0.2.0.dev0"


def test_import_raises_not_implemented():
    from maxcompute_semantic.osi.import_ import from_osi_dict

    with pytest.raises(NotImplementedError, match="deferred to v2"):
        from_osi_dict({}, db=None)

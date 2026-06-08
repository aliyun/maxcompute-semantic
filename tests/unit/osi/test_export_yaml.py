# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""dump_yaml writes UTF-8 YAML and round-trips through a parser."""

from pathlib import Path

from maxcompute_semantic.osi import dump_yaml, to_osi_dict
from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML(typ="safe")
    y.default_flow_style = False
    return y


def test_dump_yaml_writes_file(tmp_path, small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    dest = tmp_path / "demo.osi.yaml"
    dump_yaml(out, dest)
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_dump_yaml_round_trips(tmp_path, small_package_db):
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    dest = tmp_path / "demo.osi.yaml"
    dump_yaml(out, dest)
    parsed = _yaml().load(dest.read_text(encoding="utf-8"))
    assert parsed == out


def test_export_matches_golden(small_package_db):
    golden_path = (
        Path(__file__).resolve().parent.parent.parent
        / "fixtures"
        / "osi"
        / "expected_export_small.yaml"
    )
    out = to_osi_dict(small_package_db, semantic_model_name="demo")
    golden = _yaml().load(golden_path.read_text(encoding="utf-8"))
    assert out == golden, (
        "Adapter output diverged from golden. "
        "If the change is intentional, regenerate the golden by re-running "
        "the small_package_db fixture setup and calling "
        "dump_yaml(to_osi_dict(db, semantic_model_name='demo'), golden_path)."
    )

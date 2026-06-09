# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Tests for _internal/output.py — Renderer (plain/json)."""

from __future__ import annotations

import json

import pytest
from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.mc_client.errors import McsError


class TestRendererPlain:
    def test_success_plain(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="plain")
        r.success({"name": "meta-dev", "project": "meta_dev"})
        captured = capsys.readouterr()
        assert "meta-dev" in captured.out
        assert "meta_dev" in captured.out
        assert captured.err == ""

    def test_success_plain_quiet(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="plain", quiet=True)
        r.success({"name": "meta-dev"})
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_error_plain_to_stderr(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="plain")
        r.error(McsError("something broke", remediation="try again"))
        captured = capsys.readouterr()
        assert "something broke" in captured.err
        assert "try again" in captured.err
        assert captured.out == ""

    def test_error_plain_quiet_still_shows_stderr(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="plain", quiet=True)
        r.error(McsError("something broke"))
        captured = capsys.readouterr()
        assert "something broke" in captured.err

    def test_table_plain(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="plain")
        r.table(
            headers=["Name", "Project"],
            rows=[["meta-dev", "meta_dev"], ["sales-dw", "sales_dw"]],
        )
        captured = capsys.readouterr()
        assert "Name" in captured.out
        assert "meta-dev" in captured.out
        assert "sales-dw" in captured.out

    def test_table_plain_quiet_suppresses_output(self, capsys: pytest.CaptureFixture) -> None:
        """Table in quiet+plain mode produces no output."""
        r = Renderer(format="plain", quiet=True)
        r.table(headers=["Name"], rows=[["x"]])
        captured = capsys.readouterr()
        assert captured.out == ""


class TestRendererJson:
    def test_success_json(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="json")
        r.success({"name": "meta-dev", "project": "meta_dev"})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "success"
        assert data["data"]["name"] == "meta-dev"

    def test_error_json_to_stdout(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="json")
        r.error(McsError("something broke", remediation="try again"))
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "error"
        assert data["error"]["message"] == "something broke"
        assert data["error"]["remediation"] == "try again"
        assert captured.err == ""

    def test_table_json(self, capsys: pytest.CaptureFixture) -> None:
        r = Renderer(format="json")
        r.table(
            headers=["Name", "Project"],
            rows=[["meta-dev", "meta_dev"], ["sales-dw", "sales_dw"]],
        )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "success"
        assert len(data["data"]["rows"]) == 2


class TestQuietEssential:
    def test_quiet_essential_prints_key_value_in_quiet_plain(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """quiet_essential in quiet+plain mode prints just the value, one line."""
        r = Renderer(format="plain", quiet=True)
        r.quiet_essential({"name": "meta-dev"}, "name")
        captured = capsys.readouterr()
        assert captured.out == "meta-dev\n"

    def test_quiet_essential_no_output_when_not_quiet(self, capsys: pytest.CaptureFixture) -> None:
        """quiet_essential does nothing in non-quiet plain mode."""
        r = Renderer(format="plain", quiet=False)
        r.quiet_essential({"name": "meta-dev"}, "name")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_quiet_essential_no_output_in_json_mode(self, capsys: pytest.CaptureFixture) -> None:
        """quiet_essential is ignored in json mode (envelope always emitted)."""
        r = Renderer(format="json", quiet=True)
        r.quiet_essential({"name": "meta-dev"}, "name")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_quiet_essential_missing_key_no_output(self, capsys: pytest.CaptureFixture) -> None:
        """quiet_essential with missing key produces no output."""
        r = Renderer(format="plain", quiet=True)
        r.quiet_essential({"name": "meta-dev"}, "project")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_quiet_essential_none_value_no_output(self, capsys: pytest.CaptureFixture) -> None:
        """quiet_essential with None value produces no output."""
        r = Renderer(format="plain", quiet=True)
        r.quiet_essential({"profile": None}, "profile")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_quiet_essential_converts_value_to_string(self, capsys: pytest.CaptureFixture) -> None:
        """quiet_essential converts non-string values to string."""
        r = Renderer(format="plain", quiet=True)
        r.quiet_essential({"count": 42}, "count")
        captured = capsys.readouterr()
        assert captured.out == "42\n"

    def test_quiet_essential_multiple_calls_one_per_line(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Multiple quiet_essential calls each produce a separate line."""
        r = Renderer(format="plain", quiet=True)
        r.quiet_essential({"name": "alpha"}, "name")
        r.quiet_essential({"name": "beta"}, "name")
        captured = capsys.readouterr()
        assert captured.out == "alpha\nbeta\n"

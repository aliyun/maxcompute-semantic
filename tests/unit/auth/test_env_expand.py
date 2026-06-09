"""Tests for auth/env_expand.py."""

from __future__ import annotations

import pytest
from maxcompute_semantic.auth.env_expand import expand_env
from maxcompute_semantic.auth.errors import ConfigEnvNotSetError


def test_literal_value_returned_as_is() -> None:
    assert expand_env("FooAKID12345") == "FooAKID12345"


def test_env_var_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AK", "FooAKID12345")
    assert expand_env("${env:MY_AK}") == "FooAKID12345"


def test_unset_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(ConfigEnvNotSetError, match="MISSING_VAR"):
        expand_env("${env:MISSING_VAR}")


def test_empty_env_var_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMPTY_VAR", "")
    with pytest.raises(ConfigEnvNotSetError):
        expand_env("${env:EMPTY_VAR}")


def test_malformed_env_syntax_returned_as_literal() -> None:
    assert expand_env("$env:VAR") == "$env:VAR"
    assert expand_env("${env: SPACE}") == "${env: SPACE}"


def test_lowercase_env_var_name_rejected() -> None:
    assert expand_env("${env:my_var}") == "${env:my_var}"

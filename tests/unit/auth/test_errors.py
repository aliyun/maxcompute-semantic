"""Tests for auth/errors.py."""

from __future__ import annotations

import pytest
from maxcompute_semantic.auth.errors import (
    AuthBinaryMissingError,
    AuthFailedError,
    ConfigEnvNotSetError,
    ConfigPermissionError,
    ConfigWriteError,
    IncompatibleProfileVersionError,
    InvalidProfileError,
    InvalidProfileFileError,
    NoProfilesConfiguredError,
    ProfileNotFoundError,
    WorkingDirectoryError,
)
from maxcompute_semantic.mc_client.errors import McsError


@pytest.mark.parametrize(
    "cls,exit_code",
    [
        (AuthBinaryMissingError, 4),
        (AuthFailedError, 4),
        (ConfigEnvNotSetError, 3),
        (ConfigPermissionError, 3),
        (ConfigWriteError, 3),
        (IncompatibleProfileVersionError, 3),
        (InvalidProfileError, 3),
        (InvalidProfileFileError, 3),
        (NoProfilesConfiguredError, 3),
        (ProfileNotFoundError, 3),
        (WorkingDirectoryError, 1),
    ],
)
def test_subclass_is_mcs_error_with_exit_code(cls, exit_code) -> None:
    err = cls("test")
    assert isinstance(err, McsError)
    assert err.exit_code == exit_code

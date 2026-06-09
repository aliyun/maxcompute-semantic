# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Vocabulary map symmetry + completeness tests."""

from maxcompute_semantic.osi.vocabulary import (
    CUSTOM_EXTENSION_FIELDS,
    CUSTOM_EXTENSION_VENDOR,
    MCS_TO_OSI,
    OSI_TO_MCS,
)


def test_maps_are_bijective():
    # No two mcs terms collapse onto the same OSI term, and vice-versa.
    assert len(set(MCS_TO_OSI.values())) == len(MCS_TO_OSI)
    assert len(set(OSI_TO_MCS.values())) == len(OSI_TO_MCS)


def test_maps_are_inverse_of_each_other():
    for mcs, osi in MCS_TO_OSI.items():
        assert OSI_TO_MCS[osi] == mcs
    for osi, mcs in OSI_TO_MCS.items():
        assert MCS_TO_OSI[mcs] == osi


def test_custom_extension_fields_disjoint_from_native_map():
    # A field is either OSI-native (in MCS_TO_OSI) or mcs-only
    # (in CUSTOM_EXTENSION_FIELDS) — never both.
    overlap = CUSTOM_EXTENSION_FIELDS & set(MCS_TO_OSI.keys())
    assert overlap == set(), f"fields claim both native + custom: {overlap}"


def test_vendor_constant_is_uppercase_snake():
    assert CUSTOM_EXTENSION_VENDOR == "MAXCOMPUTE_SEMANTIC"

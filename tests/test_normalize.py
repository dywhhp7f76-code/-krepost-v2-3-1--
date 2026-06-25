"""Tests for krepost.security.normalize"""

import pytest
from krepost.security.normalize import (
    canonicalize_for_hash,
    normalize_for_scanning,
    NORMALIZATION_VERSION,
)


class TestNormalizationVersion:
    def test_version_format(self):
        parts = NORMALIZATION_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_version_is_2_2_0(self):
        assert NORMALIZATION_VERSION == "2.2.0"


class TestCanonicalizeForHash:
    def test_empty_string(self):
        assert canonicalize_for_hash("") == ""

    def test_simple_text(self):
        result = canonicalize_for_hash("Hello World")
        assert result == "hello world"

    def test_idempotent(self):
        text = "Some​ Te‌xt"
        r1 = canonicalize_for_hash(text)
        r2 = canonicalize_for_hash(r1)
        assert r1 == r2

    def test_zero_width_removal(self):
        text = "hel​lo"  # zero-width space
        assert canonicalize_for_hash(text) == "hello"

    def test_bom_removal(self):
        text = "﻿hello"
        assert canonicalize_for_hash(text) == "hello"

    def test_bidi_removal(self):
        text = "he‎llo"  # LTR mark
        assert canonicalize_for_hash(text) == "hello"

    def test_nbsp_removal(self):
        text = "hello world"
        result = canonicalize_for_hash(text)
        # NBSP is mapped to None (removed), not replaced with space
        assert result == "helloworld"

    def test_narrow_nbsp_removal(self):
        text = "hello world"
        result = canonicalize_for_hash(text)
        assert result == "helloworld"

    def test_casefold(self):
        assert canonicalize_for_hash("HELLO") == "hello"

    def test_whitespace_collapse(self):
        assert canonicalize_for_hash("hello   world") == "hello world"

    def test_whitespace_strip(self):
        assert canonicalize_for_hash("  hello  ") == "hello"

    def test_cyrillic_homoglyphs(self):
        # Cyrillic а,е,о should map to latin a,e,o
        cyrillic = "аео"  # а е о
        result = canonicalize_for_hash(cyrillic)
        assert result == "aeo"

    def test_cyrillic_upper_homoglyphs(self):
        cyrillic = "А"  # А (Cyrillic)
        result = canonicalize_for_hash(cyrillic)
        # After casefold: cyrillic а -> homoglyph -> a
        assert "a" in result

    def test_greek_homoglyphs(self):
        greek_alpha = "α"  # α
        result = canonicalize_for_hash(greek_alpha)
        assert result == "a"

    def test_digit_homoglyphs(self):
        assert canonicalize_for_hash("0") == "o"
        assert canonicalize_for_hash("1") == "i"
        assert canonicalize_for_hash("5") == "s"

    def test_pipe_homoglyph(self):
        assert canonicalize_for_hash("|") == "i"

    def test_nfkc_normalization(self):
        # ﬁ ligature should decompose to fi
        assert canonicalize_for_hash("ﬁ") == "fi"

    def test_mixed_attack(self):
        # zero-width + cyrillic homoglyphs + extra whitespace
        text = "​hel‌loа  wоrld‍"
        result = canonicalize_for_hash(text)
        assert "​" not in result
        assert "‌" not in result
        assert "‍" not in result
        assert "  " not in result

    def test_deterministic(self):
        text = "test input with ​ zero-width"
        results = {canonicalize_for_hash(text) for _ in range(100)}
        assert len(results) == 1


class TestNormalizeForScanning:
    def test_empty_string(self):
        assert normalize_for_scanning("") == ""

    def test_soft_mode_preserves_cyrillic(self):
        cyrillic = "аео"  # а е о
        result = normalize_for_scanning(cyrillic, soft=True)
        # soft mode should NOT apply homoglyph mapping
        assert result != "aeo"

    def test_hard_mode_maps_cyrillic(self):
        cyrillic = "аео"  # а е о
        result = normalize_for_scanning(cyrillic, soft=False)
        assert result == "aeo"

    def test_zero_width_removed_in_both_modes(self):
        text = "hel​lo"
        assert "​" not in normalize_for_scanning(text, soft=True)
        assert "​" not in normalize_for_scanning(text, soft=False)

    def test_casefold_in_both_modes(self):
        assert normalize_for_scanning("HELLO", soft=True) == "hello"
        assert normalize_for_scanning("HELLO", soft=False) == "hello"

    def test_whitespace_collapse(self):
        assert normalize_for_scanning("a   b", soft=False) == "a b"

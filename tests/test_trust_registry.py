"""Tests for krepost.security.trust_registry"""

import asyncio
import tempfile
import pytest
from pathlib import Path

from krepost.security.trust_registry import TrustRegistry


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test_trust.db"


@pytest.fixture
def registry(tmp_db):
    return TrustRegistry(db_path=tmp_db)


class TestTrustRegistry:
    def test_db_created(self, tmp_db, registry):
        assert tmp_db.exists()

    def test_compute_hash_deterministic(self, registry):
        h1 = registry._compute_hash("hello")
        h2 = registry._compute_hash("hello")
        assert h1 == h2

    def test_compute_hash_different_inputs(self, registry):
        h1 = registry._compute_hash("hello")
        h2 = registry._compute_hash("world")
        assert h1 != h2

    def test_compute_hash_normalizes(self, registry):
        # Texts that differ only by zero-width chars should hash the same
        h1 = registry._compute_hash("hello")
        h2 = registry._compute_hash("hel​lo")
        assert h1 == h2

    @pytest.mark.asyncio
    async def test_untrusted_by_default(self, registry):
        assert await registry.is_trusted("some text") is False

    @pytest.mark.asyncio
    async def test_add_and_check_trusted(self, registry):
        await registry.add_trusted("trusted text", source_name="test")
        assert await registry.is_trusted("trusted text") is True

    @pytest.mark.asyncio
    async def test_revoke_trusted(self, registry):
        await registry.add_trusted("revokable", source_name="test")
        assert await registry.is_trusted("revokable") is True
        await registry.revoke_trusted("revokable")
        assert await registry.is_trusted("revokable") is False

    @pytest.mark.asyncio
    async def test_re_add_after_revoke(self, registry):
        await registry.add_trusted("text", source_name="v1")
        await registry.revoke_trusted("text")
        assert await registry.is_trusted("text") is False
        await registry.add_trusted("text", source_name="v2")
        assert await registry.is_trusted("text") is True

    @pytest.mark.asyncio
    async def test_add_idempotent(self, registry):
        await registry.add_trusted("same", source_name="first")
        await registry.add_trusted("same", source_name="second")
        assert await registry.is_trusted("same") is True

    @pytest.mark.asyncio
    async def test_fail_closed_on_bad_db(self, tmp_path):
        bad_path = tmp_path / "nonexistent_dir" / "sub" / "trust.db"
        # Constructor creates parent dirs, so use a file as path to cause errors
        reg = TrustRegistry(db_path=tmp_path / "test.db")
        # Corrupt the DB
        (tmp_path / "test.db").write_text("NOT A DATABASE")
        # Should fail-closed (return False), not raise
        result = await reg.is_trusted("anything")
        assert result is False

    @pytest.mark.asyncio
    async def test_normalized_trust_lookup(self, registry):
        await registry.add_trusted("hello world", source_name="test")
        # Same text with zero-width chars should also be trusted
        assert await registry.is_trusted("hel​lo world") is True

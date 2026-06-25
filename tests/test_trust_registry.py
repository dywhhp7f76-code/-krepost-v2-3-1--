"""Тесты TrustRegistry — register/verify/revoke/zone enforcement."""
import tempfile
from pathlib import Path

from krepost.security import TrustRegistry


def _make_registry(tmp_path: Path) -> TrustRegistry:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "ingested").mkdir()
    db = tmp_path / "test.db"
    return TrustRegistry(db_path=db, vault_root=vault, ingested_subdir="ingested")


class TestRegisterAndVerify:
    def test_register_then_trusted(self, tmp_path):
        reg = _make_registry(tmp_path)
        note = tmp_path / "vault" / "my_note.md"
        note.write_text("Hello world", encoding="utf-8")
        reg.register(str(note), "Hello world")
        assert reg.is_trusted(str(note), "Hello world")

    def test_unregistered_not_trusted(self, tmp_path):
        reg = _make_registry(tmp_path)
        assert not reg.is_trusted(str(tmp_path / "vault" / "unknown.md"), "some text")

    def test_wrong_hash_not_trusted(self, tmp_path):
        reg = _make_registry(tmp_path)
        note = tmp_path / "vault" / "note.md"
        note.write_text("original", encoding="utf-8")
        reg.register(str(note), "original")
        assert not reg.is_trusted(str(note), "modified content")

    def test_update_reregisters(self, tmp_path):
        reg = _make_registry(tmp_path)
        note = tmp_path / "vault" / "note.md"
        note.write_text("v1", encoding="utf-8")
        reg.register(str(note), "v1")
        assert reg.is_trusted(str(note), "v1")
        reg.register(str(note), "v2")
        assert reg.is_trusted(str(note), "v2")
        assert not reg.is_trusted(str(note), "v1")


class TestForget:
    def test_forget_removes(self, tmp_path):
        reg = _make_registry(tmp_path)
        note = tmp_path / "vault" / "note.md"
        note.write_text("text", encoding="utf-8")
        reg.register(str(note), "text")
        assert reg.is_trusted(str(note), "text")
        reg.forget(str(note))
        assert not reg.is_trusted(str(note), "text")

    def test_forget_nonexistent_ok(self, tmp_path):
        reg = _make_registry(tmp_path)
        reg.forget(str(tmp_path / "vault" / "nonexistent.md"))


class TestZoneEnforcement:
    def test_ingested_zone_blocked(self, tmp_path):
        reg = _make_registry(tmp_path)
        ingested_note = tmp_path / "vault" / "ingested" / "external.md"
        ingested_note.write_text("external doc", encoding="utf-8")
        reg.register(str(ingested_note), "external doc")
        assert not reg.is_trusted(str(ingested_note), "external doc")

    def test_outside_vault_not_trusted(self, tmp_path):
        reg = _make_registry(tmp_path)
        outside = tmp_path / "elsewhere" / "note.md"
        outside.parent.mkdir()
        outside.write_text("text", encoding="utf-8")
        assert not reg.is_trusted(str(outside), "text")

    def test_path_traversal_blocked(self, tmp_path):
        reg = _make_registry(tmp_path)
        traversal = str(tmp_path / "vault" / "ingested" / ".." / "trick.md")
        note = tmp_path / "vault" / "trick.md"
        note.write_text("trick", encoding="utf-8")
        reg.register(str(note), "trick")
        assert reg.is_trusted(traversal, "trick")


class TestCount:
    def test_count_reflects_registrations(self, tmp_path):
        reg = _make_registry(tmp_path)
        assert reg.count() == 0
        for i in range(3):
            note = tmp_path / "vault" / f"note_{i}.md"
            note.write_text(f"text {i}", encoding="utf-8")
            reg.register(str(note), f"text {i}")
        assert reg.count() == 3


class TestBootstrap:
    def test_bootstrap_registers_existing(self, tmp_path):
        reg = _make_registry(tmp_path)
        vault = tmp_path / "vault"
        for i in range(3):
            (vault / f"note_{i}.md").write_text(f"content {i}", encoding="utf-8")
        (vault / "ingested" / "external.md").write_text("external", encoding="utf-8")
        n = reg.bootstrap()
        assert n == 3
        assert reg.count() == 3

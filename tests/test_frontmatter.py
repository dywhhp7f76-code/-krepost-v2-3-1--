"""Тесты parse_existing_frontmatter — общий контракт ingestion/trust_bridge."""
from krepost.utils.frontmatter import parse_existing_frontmatter


def test_valid_frontmatter():
    content = "---\ntitle: Test\nstatus: draft\n---\nBody text here."
    meta, body = parse_existing_frontmatter(content)
    assert meta == {"title": "Test", "status": "draft"}
    assert body == "Body text here."


def test_no_frontmatter():
    content = "Just plain text without frontmatter."
    meta, body = parse_existing_frontmatter(content)
    assert meta == {}
    assert body == content


def test_broken_yaml():
    content = "---\n: [invalid yaml\n---\nBody."
    meta, body = parse_existing_frontmatter(content)
    assert meta == {}
    assert body == content


def test_bom_stripped():
    content = "﻿---\nkey: value\n---\nBody after BOM."
    meta, body = parse_existing_frontmatter(content)
    assert meta == {"key": "value"}
    assert body == "Body after BOM."


def test_no_closing_separator():
    content = "---\ntitle: Test\nNo closing separator here."
    meta, body = parse_existing_frontmatter(content)
    assert meta == {}
    assert body == content


def test_empty_frontmatter():
    content = "---\n\n---\nBody."
    meta, body = parse_existing_frontmatter(content)
    assert meta == {}
    assert body == "Body."


def test_non_dict_yaml():
    content = "---\n- item1\n- item2\n---\nBody."
    meta, body = parse_existing_frontmatter(content)
    assert meta == {}
    assert body == content


def test_security_fields_preserved():
    content = "---\nsource: external\nquarantine: true\ncustom: data\n---\nBody."
    meta, body = parse_existing_frontmatter(content)
    assert meta["source"] == "external"
    assert meta["quarantine"] is True
    assert meta["custom"] == "data"
    assert body == "Body."

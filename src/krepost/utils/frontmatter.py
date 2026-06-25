"""Общий парсер frontmatter для модулей Крепости."""
from __future__ import annotations

import re

import yaml


def parse_existing_frontmatter(content: str) -> tuple[dict, str]:
    """
    Строгий парс frontmatter: только если контент начинается с '---\\n'
    И есть закрывающий '---'. Возвращает (поля, тело_без_frontmatter).
    Если frontmatter нет/битый — ({}, исходный_контент).
    """
    stripped = content.lstrip("﻿")  # снять BOM
    if not stripped.startswith("---"):
        return {}, content
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", stripped, re.DOTALL)
    if not m:
        return {}, content
    try:
        parsed = yaml.safe_load(m.group(1)) or {}
        if not isinstance(parsed, dict):
            return {}, content
    except yaml.YAMLError:
        return {}, content
    return parsed, m.group(2)

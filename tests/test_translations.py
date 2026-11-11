"""Tests of the dialog translations."""

import json
from pathlib import Path
from typing import Any

import pytest

TRANSLATIONS = (
    Path(__file__).parents[1] / "custom_components/backdated_sensor/translations"
)


def _keys(content: Any, prefix: str = "") -> set[str]:
    """Return the paths of all texts."""
    if isinstance(content, dict):
        return {
            path
            for key, value in content.items()
            for path in _keys(value, f"{prefix}{key}.")
        }
    return {prefix.rstrip(".")}


@pytest.mark.parametrize(
    "path", sorted(TRANSLATIONS.glob("*.json")), ids=lambda path: path.stem
)
def test_every_language_has_the_texts_of_english(path: Path) -> None:
    """A language lacks no text and has none that English does not have."""
    english = json.loads((TRANSLATIONS / "en.json").read_text(encoding="utf-8"))
    content = json.loads(path.read_text(encoding="utf-8"))
    assert _keys(content) == _keys(english)

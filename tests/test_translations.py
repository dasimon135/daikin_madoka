"""Every shipped language covers every string the integration can show.

Home Assistant falls back to English key by key, so a missing key never
raises: it surfaces as one English sentence in an otherwise translated repair,
which is how nine Italian strings went missing unnoticed.
"""

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "daikin_madoka"
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))


def _keys(node: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            keys |= _keys(value, f"{path}.")
        else:
            keys.add(path)
    return keys


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_a_translation_has_exactly_the_keys_of_strings_json(path: Path) -> None:
    reference = _keys(json.loads((COMPONENT / "strings.json").read_text("utf-8")))
    translated = _keys(json.loads(path.read_text("utf-8")))

    assert reference - translated == set(), "missing keys"
    assert translated - reference == set(), "keys strings.json does not have"

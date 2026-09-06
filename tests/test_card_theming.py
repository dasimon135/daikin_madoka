"""The bundled card must be themeable.

Two properties, both checked on the shipped file so they cannot regress
silently:

1. The card's outer container is a real ``<ha-card>``. Theme variables cross
   the shadow DOM on their own, but anything Home Assistant or card-mod applies
   *to the card element* (``ha-card-box-shadow``, a theme's ``card-mod-card``
   block, a user's per-card ``card_mod``) selects ``ha-card`` — a plain
   ``<div>`` imitating one is invisible to all of it.

2. No frozen colour. Every colour literal in the file must be the fallback of a
   ``var(--madoka-*, …)`` (or of a Home Assistant variable), so a theme can
   repaint the card while the default rendering stays exactly what it is today.
"""

import re
from pathlib import Path

CARD = Path(__file__).parents[1] / "custom_components" / "daikin_madoka" / "frontend" / "madoka-card.js"

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# a literal is acceptable when it is the fallback of a CSS variable on the same line
FALLBACK = re.compile(r"var\(\s*--[a-z0-9-]+\s*,\s*(?:var\([^)]*,\s*)?#[0-9a-fA-F]{3,8}\b")
# the console banner printed at load time styles a console.info() call, not the card: its two
# style arguments are JS string literals on their own lines, which no CSS line of the templates is
ALLOWED_LINE_MARKERS = ("console.info", "%c")


def _is_console_banner_argument(line: str) -> bool:
    return line.strip().startswith('"color:#')


def _source() -> str:
    return CARD.read_text(encoding="utf-8")


def test_card_container_is_a_real_ha_card() -> None:
    src = _source()
    assert '<div class="card' not in src, "the card container must be <ha-card>, not a <div> imitating one"
    assert src.count('<ha-card class="card') == 2, "both layouts (full and tile) must render an <ha-card>"
    assert "</ha-card>" in src


def test_every_colour_literal_is_a_variable_fallback() -> None:
    frozen: list[str] = []
    for n, line in enumerate(_source().splitlines(), 1):
        if any(marker in line for marker in ALLOWED_LINE_MARKERS) or _is_console_banner_argument(line):
            continue
        literals = HEX.findall(line)
        if not literals:
            continue
        # every literal on the line must be matched by a fallback pattern
        covered = FALLBACK.findall(line)
        if len(covered) < len(literals):
            frozen.append(f"{n}: {line.strip()[:100]}")
    assert not frozen, "hard-coded colours (not a var() fallback):\n" + "\n".join(frozen)


def test_no_rgb_literals_outside_shadows() -> None:
    """rgba() literals are tolerated only for pure black shadows/scrims."""
    bad = []
    for n, line in enumerate(_source().splitlines(), 1):
        for m in re.finditer(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", line):
            if m.groups() != ("0", "0", "0"):
                bad.append(f"{n}: {m.group(0)}")
    assert not bad, "coloured rgb() literals:\n" + "\n".join(bad)

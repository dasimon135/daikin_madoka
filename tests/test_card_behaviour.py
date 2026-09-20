"""Behaviour of the bundled card, run under Node with a minimal DOM stub.

The card had no behavioural test at all: test_card_theming.py reads the source
as text. tests/card_harness.js loads the shipped file, drives a few scenarios
and prints their results as JSON. Skipped where Node is not installed (GitHub's
runners ship it).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "card_harness.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def results() -> dict:
    proc = subprocess.run(
        [NODE, str(HARNESS)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_rapid_presses_accumulate_into_one_write(results: dict) -> None:
    """+ + + from 25 is one write of 28, not three writes of 26."""
    assert results["rapid_presses_accumulate"] == [28]


def test_no_setpoint_is_written_outside_a_setpoint_mode(results: dict) -> None:
    assert results["no_setpoint_write_in_fan_mode"] == 0


def test_changing_the_layout_rebuilds_the_card(results: dict) -> None:
    assert results["layout_switch_rebuilds"] == {"threw": False, "tile": True}


def test_an_unrelated_state_change_does_not_re_render(results: dict) -> None:
    assert results["unrelated_state_change_is_skipped"] == 0


def test_siblings_are_found_whatever_the_entity_id_language(results: dict) -> None:
    assert results["siblings_found_by_translation_key"] == {
        "outdoor": "sensor.salone_temperatura_esterna",
        "indoor": "sensor.salone_temperatura_interna",
    }


def test_attribute_values_are_escaped_before_reaching_the_dom(results: dict) -> None:
    assert results["fan_mode_markup_is_escaped"] is False


def test_the_readout_follows_the_presses_before_ha_confirms(results: dict) -> None:
    assert results["pending_target_is_shown"] is True


def test_the_pending_target_is_dropped_once_the_entity_reports_it(results: dict) -> None:
    assert results["pending_target_clears_when_confirmed"] is None


def test_the_dial_disables_its_bump_buttons_without_a_setpoint(results: dict) -> None:
    """Plus and minus go, power stays: it is how you leave fan mode."""
    assert results["bump_buttons_disabled_without_a_setpoint"] == [True, True, False]


def test_the_graph_markers_are_absolute_hours(results: dict) -> None:
    """Issue #100: no mental arithmetic, and no mixing with the absolute "now"."""
    result = results["graph_times_are_absolute_hours"]
    assert result["relative"] is False
    assert all(":" in label for label in result["labels"]), result["labels"]

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


def test_an_unknown_hvac_state_is_escaped_before_reaching_the_dom(results: dict) -> None:
    assert results["mode_label_markup_is_escaped"] is False


def test_power_on_lets_the_device_resume_its_own_mode(results: dict) -> None:
    """A heating user switched off in winter must not get the cooling back.

    Without TURN_ON the card still has to guess, and keeps its old guess.
    """
    assert results["power_on_resumes_the_device_mode"] == {
        "turnOn": ["turn_on"],
        "noTurnOn": ["set_hvac_mode:cool"],
    }


def test_power_off_uses_turn_off_when_the_entity_supports_it(results: dict) -> None:
    assert results["power_off_uses_turn_off_when_supported"] == {
        "turnOff": ["turn_off"],
        "noTurnOff": ["set_hvac_mode:off"],
    }


def test_a_press_is_sent_even_if_the_card_is_closed_before_the_delay(results: dict) -> None:
    assert results["press_then_close_still_sends"] == [26]


def test_a_rejected_write_does_not_stay_on_screen(results: dict) -> None:
    assert results["rejected_write_drops_the_pending_target"] == {
        "unhandled": 0,
        "pending": None,
        "shows25": True,
    }


def test_an_unconfirmed_target_is_redrawn_away_when_it_expires(results: dict) -> None:
    assert results["pending_expiry_redraws"] is True


def test_the_entity_step_drives_bumps_and_display(results: dict) -> None:
    result = results["half_degree_step"]
    assert result["sent"] == [21.5], result
    assert result["dial"] is True, result
    assert result["tile"] is True, result


def test_a_range_bump_never_puts_high_below_low(results: dict) -> None:
    assert results["range_high_never_below_low"] == [
        {"entity_id": "climate.salon", "target_temp_low": 22, "target_temp_high": 22}
    ]


def test_the_readout_follows_the_presses_before_ha_confirms(results: dict) -> None:
    assert results["pending_target_is_shown"] is True


def test_the_pending_target_is_dropped_once_the_entity_reports_it(results: dict) -> None:
    assert results["pending_target_clears_when_confirmed"] is None


def test_the_dial_disables_its_bump_buttons_without_a_setpoint(results: dict) -> None:
    """Plus and minus go, power stays: it is how you leave fan mode."""
    assert results["bump_buttons_disabled_without_a_setpoint"] == [True, True, False]


def test_the_graph_markers_are_absolute_hours(results: dict) -> None:
    """No offsets to work out in your head; every label names a clock time.

    Their exact shape is the next test's business.
    """
    result = results["graph_times_are_absolute_hours"]
    assert result["relative"] is False
    assert result["labels"], result


def test_the_graph_markers_sit_on_round_hours(results: dict) -> None:
    """Only the newest reading carries minutes; the rest are whole hours."""
    result = results["graph_times_are_round_hours"]
    assert len(result["withMinutes"]) == 1, result["labels"]
    assert result["withMinutes"][0] == result["labels"][-1]
    assert len(result["labels"]) >= 3, result["labels"]


def test_the_popup_keeps_the_options_set_on_the_tile(results: dict) -> None:
    """Everything but the layout, which is the whole point of the popup."""
    assert results["popup_keeps_the_card_options"] == {
        "layout": "full",
        "show_graph_times": True,
        "show_decimals": True,
        "outdoor_entity": "sensor.outside",
        "name": "Salon",
    }


def test_no_hour_mark_prints_over_the_newest_reading(results: dict) -> None:
    """The reporter's own 09:05 to 19:29 window: "19 h" collided with "19:29"."""
    result = results["graph_marks_do_not_overprint_the_last_reading"]
    assert "19 h" not in result["labels"], result
    assert result["labels"][-1] == "19:29", result
    assert "16 h" in result["labels"], result
    # Every mark before the last stays clear of the right-aligned final label.
    assert all(left <= 80 for left in result["lefts"][:-1]), result

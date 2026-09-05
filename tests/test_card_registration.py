"""The bundled card must be served before any config entry sets up.

Field report 2026-08-28: the Madoka card intermittently rendered as
"custom element doesn't exist: madoka-card", with no pattern the user could
pin down.

The card is registered as a dashboard resource pointing at
``/daikin_madoka/madoka-card.js``, and the frontend requests that URL on every
page load. The static path behind it, however, was registered from
``async_setup_entry`` -- which only runs once the Bluetooth stack is up and the
thermostats have had their chance to answer. Between Home Assistant accepting
HTTP requests and the first entry finishing, the URL returned 404, the browser
parsed the error page as a JavaScript module, and the custom element was never
defined. The card stayed broken on that page until it was reloaded by hand.

Opening a dashboard right after restarting Home Assistant is precisely how one
lands in that window, which is what made the failure look random.

This pins the fix where the browser sees it: with no config entry configured
at all, the URL answers.
"""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.daikin_madoka import frontend
from custom_components.daikin_madoka.const import DOMAIN
from custom_components.daikin_madoka.frontend import CARD_URL, async_register_card


def _resources(hass: HomeAssistant) -> list[dict]:
    return list(hass.data[LOVELACE_DATA].resources.async_items())


async def _setup(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "lovelace", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Setting the component up pulls in the bluetooth integration, whose
    scanner schedules a device-expiry timer that outlives the test; it is HA's
    own bookkeeping, not ours (same waiver as test_degraded_load)."""
    return True


async def test_card_is_served_without_any_config_entry(
    hass: HomeAssistant, hass_client, mock_bluetooth
) -> None:
    """Component setup alone must make the card URL answer 200.

    This is the regression. With the registration living in
    ``async_setup_entry``, no entry means no route, and the dashboard resource
    pointing at this URL gets an HTML error page that the browser then fails to
    parse as a module -- the "custom element doesn't exist" the user reported.
    """
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    client = await hass_client()
    response = await client.get(CARD_URL)

    assert response.status == 200
    assert "madoka-card" in await response.text()


async def test_registering_the_card_twice_is_a_no_op(
    hass: HomeAssistant, hass_client, mock_bluetooth
) -> None:
    """A second registration must not raise.

    Component setup now serves the card, but ``async_register_card`` stays
    callable from anywhere. Registering the same static path twice is an error
    at the HTTP layer, so the guard that makes the second call a no-op is what
    keeps a reload from taking the integration down with it.
    """
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    await async_register_card(hass)

    client = await hass_client()
    assert (await client.get(CARD_URL)).status == 200


async def test_the_card_is_registered_as_a_lovelace_resource(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """Lovelace waits for its own resources; nothing waits for a module URL.

    Field report 2026-09-04, and independently ha-rf-fan#44 from another user:
    a card handed to the frontend's extra-module list renders as a
    configuration error in the Android companion app every time, and on about
    one hard reload in three in a desktop browser -- while the same card
    registered as a dashboard resource works every time. Clearing the app's
    data changes nothing, so this is not a cache: Lovelace loads its own
    resources and waits for them before rendering a card, and nothing waits for
    an extra module URL.
    """
    await _setup(hass)

    ours = [item for item in _resources(hass) if item["url"].split("?")[0] == CARD_URL]

    assert ours, f"the card was not registered as a resource: {_resources(hass)}"
    assert ours[0]["type"] == "module"


async def test_it_is_registered_once_however_often_setup_runs(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """A resource is persistent, so a second registration is a second copy.

    Two copies race to define the same element and the loser cannot replace it,
    which leaves whichever build won rendering forever (ha-rf-fan#29, #44).
    Registering again with the URL already in the store must adopt it.
    """
    await _setup(hass)
    ours = [item for item in _resources(hass) if item["url"].split("?")[0] == CARD_URL]
    assert await frontend._async_register_resource(hass, ours[0]["url"])
    await hass.async_block_till_done()

    ours = [item for item in _resources(hass) if item["url"].split("?")[0] == CARD_URL]
    assert len(ours) == 1


async def test_an_existing_registration_is_moved_to_the_current_url(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """Including one added by hand: that copy is the one that goes stale.

    Matching on the path rather than the whole URL is what lets the integration
    adopt it instead of adding a second, competing card.
    """
    assert await async_setup_component(hass, "lovelace", {})
    resources = hass.data[LOVELACE_DATA].resources
    await resources.async_get_info()
    await resources.async_create_item(
        {"res_type": "module", "url": f"{CARD_URL}?v=stale"}
    )

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    ours = [item for item in _resources(hass) if item["url"].split("?")[0] == CARD_URL]
    assert len(ours) == 1
    assert ours[0]["url"] != f"{CARD_URL}?v=stale"


async def test_yaml_mode_falls_back_to_the_module_list(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """In YAML mode the resource list is the user's file, not ours to write.

    The old path is kept for exactly this case, and for a frontend that is not
    there at all.
    """
    assert await async_setup_component(hass, "lovelace", {})
    hass.data[LOVELACE_DATA].resource_mode = MODE_YAML

    with patch(
        "custom_components.daikin_madoka.frontend.add_extra_js_url"
    ) as add_extra_js_url:
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    add_extra_js_url.assert_called_once()
    assert add_extra_js_url.call_args[0][1].split("?")[0] == CARD_URL


async def test_auto_loaded_url_changes_with_the_card_file(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """The URL must be derived from the file content, whichever path uses it.

    ``?v=`` used to be a hand-maintained card version that did not move when
    the file changed, so a browser holding a copy of the previous card kept
    executing it across an update (ha-rf-fan#29 is the same defect). Keying it
    on a digest of the file means every shipped change is a new URL.

    This is a cache-correctness fix, not the fix for the card going missing --
    see ``test_the_card_is_registered_as_a_lovelace_resource`` for that one.
    """
    await _setup(hass)

    card = Path(frontend.__file__).parent / "frontend" / "madoka-card.js"
    digest = hashlib.sha256(card.read_bytes()).hexdigest()[:12]
    urls = [item["url"] for item in _resources(hass)]
    assert f"{CARD_URL}?v={digest}" in urls

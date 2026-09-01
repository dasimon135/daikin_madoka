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

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.daikin_madoka.const import DOMAIN
from custom_components.daikin_madoka.frontend import CARD_URL, async_register_card


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

"""Register the bundled Madoka Lovelace card with the HA frontend."""
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_VERSION = "0.3.1"
CARD_URL = f"/{DOMAIN}/madoka-card.js"
_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card file and add it as a frontend resource (once per run)."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    path = Path(__file__).parent / "frontend" / "madoka-card.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(path), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={CARD_VERSION}")
    _LOGGER.debug("Madoka card registered at %s", CARD_URL)

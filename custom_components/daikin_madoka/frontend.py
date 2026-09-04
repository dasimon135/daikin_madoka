"""Register the bundled Madoka Lovelace card with the HA frontend."""

import hashlib
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/{DOMAIN}/madoka-card.js"
_REGISTERED = f"{DOMAIN}_card_registered"


def _card_digest(path: Path) -> str:
    """Short content digest of the card file, used as its cache-buster."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card file and get the browser to load it (once per run)."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    path = Path(__file__).parent / "frontend" / "madoka-card.js"
    # cache_headers=False, deliberately: `?v=` below moves with the file, but a
    # resource somebody registered by hand carries a frozen URL, and 31 days of
    # `max-age` on it means no reload revalidates the card for a month.
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(path), cache_headers=False)]
    )

    # Cache-bust on a digest of the file rather than a version string, so every
    # shipped change of the card is a URL no browser can already be holding.
    digest = await hass.async_add_executor_job(_card_digest, path)
    url = f"{CARD_URL}?v={digest}"

    if await _async_register_resource(hass, url):
        return

    # Lovelace is not up yet, or is not there at all. Try once more when Home
    # Assistant has finished starting, and only fall back if that fails too:
    # doing both would put two copies of the card in one page, and the loser of
    # that race cannot be replaced.
    if hass.state is CoreState.running:
        _async_add_module_url(hass, url)
        return

    async def _retry(_event: Any) -> None:
        if not await _async_register_resource(hass, url):
            _async_add_module_url(hass, url)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _retry)


async def _async_register_resource(hass: HomeAssistant, url: str) -> bool:
    """Register the card as a Lovelace resource. True when it is registered.

    This is how HACS delivers every custom card, and the difference is not
    cosmetic: Lovelace loads its own resources and WAITS for them before it
    renders a card, while nothing waits for a frontend module URL. Handed to
    the module list, this card rendered as a configuration error in the Android
    companion app on every single load -- reinstalling the app changed nothing,
    so it was never a cache -- and ha-rf-fan#44 reports the same failure from
    another user, plus about one hard reload in three in a desktop browser. As
    a dashboard resource it works every time.

    Storage mode only. In YAML mode the resource list is the user's file and
    this integration has no business writing to it, so the caller falls back.
    """
    try:
        from homeassistant.components.lovelace.const import (
            LOVELACE_DATA,
            MODE_STORAGE,
        )
        from homeassistant.components.lovelace.resources import (
            ResourceStorageCollection,
        )
    except ImportError:  # pragma: no cover - lovelace is a core component
        return False

    data = hass.data.get(LOVELACE_DATA)
    if data is None or data.resource_mode != MODE_STORAGE:
        return False

    resources = data.resources
    if not isinstance(resources, ResourceStorageCollection):
        # Storage mode without a storage collection cannot happen, but the
        # write calls below only exist on that class.
        return False
    # `async_items()` does NOT read the store, while `async_create_item()` does.
    # On a start where Lovelace has not read its resources yet, the lookup would
    # answer "empty", this would conclude nothing is registered, and the create
    # would append a second copy of what was already there -- one more per
    # restart (ha-rf-fan#44). `async_get_info()` is the public way to make sure
    # the store has been read.
    await resources.async_get_info()

    # Matched on the PATH, not the whole URL: the query changes with the file,
    # and a copy the user registered by hand carries a different one (or none).
    # Adopting that copy is what keeps a hand-registered entry from becoming a
    # second, stale card.
    ours = [
        item
        for item in resources.async_items()
        if str(item.get("url", "")).split("?")[0] == CARD_URL
    ]

    if not ours:
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.debug("Registered the Madoka card as a Lovelace resource: %s", url)
        return True

    keep, *extras = ours
    if keep.get("url") != url:
        await resources.async_update_item(keep["id"], {"url": url})
        _LOGGER.debug("Updated the Madoka card resource to %s", url)
    for extra in extras:
        await resources.async_delete_item(extra["id"])
        _LOGGER.warning(
            "Removed a duplicate registration of the Madoka card (%s); two "
            "copies race to define the same element and the older one wins",
            extra.get("url"),
        )
    return True


def _async_add_module_url(hass: HomeAssistant, url: str) -> None:
    """Fall back to the frontend's extra module list.

    Kept for YAML mode and for an install where Lovelace is not there at all.
    Nothing waits for these, which is why it is the fallback and not the path.
    """
    add_extra_js_url(hass, url)
    _LOGGER.debug("Madoka card auto-loaded through the module list: %s", url)

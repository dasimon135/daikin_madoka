"""Config flow for the Daikin Madoka platform."""

import re

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_DEVICE,
    CONF_FORCE_UPDATE,
)
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_MAC, CONF_FRIENDLY_NAME


@config_entries.HANDLERS.register(DOMAIN)
class FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    @property
    def schema(self):
        """Return current schema."""
        return vol.Schema(
            {
                vol.Required(CONF_MAC): cv.string,
                vol.Optional(CONF_FRIENDLY_NAME, default=""): cv.string,
                vol.Optional(CONF_FORCE_UPDATE, default=True): bool,
                vol.Optional(CONF_DEVICE, default="hci0"): cv.string,
            }
        )

    async def _create_entry(self, mac, friendly_name, force_update, device):
        """Register new entry."""
        title = friendly_name.strip() if friendly_name.strip() else f"BRC1H {mac}"
        return self.async_create_entry(
            title=title,
            data={
                CONF_MAC: mac,
                CONF_FRIENDLY_NAME: friendly_name.strip(),
                CONF_DEVICE: device,
                CONF_FORCE_UPDATE: force_update,
            },
        )

    def validate_mac(self, mac):
        """Validate the MAC address format."""
        return bool(
            re.match(
                "[0-9a-f]{2}([-:]?)[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$", mac.lower()
            )
        )

    async def async_step_user(self, user_input=None):
        """User initiated config flow."""
        errors = {}

        if user_input is not None:
            mac = user_input[CONF_MAC].strip()
            if not self.validate_mac(mac):
                errors[CONF_MAC] = "not_a_mac"

            if not errors:
                await self.async_set_unique_id(mac.upper())
                self._abort_if_unique_id_configured()
                return await self._create_entry(
                    mac,
                    user_input.get(CONF_FRIENDLY_NAME, ""),
                    user_input.get(CONF_FORCE_UPDATE, True),
                    user_input.get(CONF_DEVICE, "hci0"),
                )

        return self.async_show_form(
            step_id="user", data_schema=self.schema, errors=errors
        )

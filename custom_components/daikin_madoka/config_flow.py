"""Config flow for the Daikin Madoka platform."""

import re

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_FRIENDLY_NAME, CONF_MAC, DEFAULT_SCAN_INTERVAL, DOMAIN

MAC_REGEX = "[0-9a-f]{2}([-:]?)[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$"


def validate_mac(mac: str) -> bool:
    """Validate the MAC address format."""
    return bool(re.match(MAC_REGEX, mac.lower()))


class FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    def _user_schema(self) -> vol.Schema:
        """Schema for manual/assisted setup: discovered devices + free MAC entry."""
        options = [
            SelectOptionDict(
                value=service_info.address,
                label=f"{service_info.name} ({service_info.address})",
            )
            for service_info in async_discovered_service_info(
                self.hass, connectable=True
            )
            if (service_info.name or "").upper().startswith("BRC1H")
        ]
        return vol.Schema(
            {
                vol.Required(CONF_MAC): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_FRIENDLY_NAME, default=""): str,
            }
        )

    async def _create_entry(self, mac: str, friendly_name: str):
        """Register new entry."""
        title = friendly_name.strip() or f"BRC1H {mac}"
        return self.async_create_entry(
            title=title,
            data={
                CONF_MAC: mac,
                CONF_FRIENDLY_NAME: friendly_name.strip(),
            },
        )

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        """Handle a thermostat discovered by Home Assistant's Bluetooth stack."""
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input=None):
        """Confirm the discovered thermostat and pick a name."""
        assert self._discovery_info is not None

        if user_input is not None:
            return await self._create_entry(
                self._discovery_info.address,
                user_input.get(CONF_FRIENDLY_NAME, ""),
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_FRIENDLY_NAME, default=""): str}),
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address
            },
        )

    async def async_step_user(self, user_input=None):
        """User initiated config flow."""
        errors = {}

        if user_input is not None:
            mac = user_input[CONF_MAC].strip()
            if not validate_mac(mac):
                errors[CONF_MAC] = "not_a_mac"

            if not errors:
                await self.async_set_unique_id(mac.upper())
                self._abort_if_unique_id_configured()
                return await self._create_entry(
                    mac,
                    user_input.get(CONF_FRIENDLY_NAME, ""),
                )

        return self.async_show_form(
            step_id="user", data_schema=self._user_schema(), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow (poll interval)."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10,
                            max=600,
                            step=5,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

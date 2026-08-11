"""Config flow for the Daikin Madoka platform."""

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_DEVICES, CONF_SCAN_INTERVAL
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

from .const import (
    BRC1H_NAME_PREFIX,
    CONF_BONDED_SOURCES,
    CONF_DEVICE_TYPE,
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PAIRING_STATE,
    CONF_PREFERRED_SOURCE,
    DEFAULT_DEVICE_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPE_VENTILATION,
    DOMAIN,
    MADOKA_SERVICE_UUID,
    PAIRING_WINDOW_TIMEOUT,
    RSSI_DISCOVERY_FLOOR,
    VALIDATE_TIMEOUT,
)
from .coordinator import async_forget_pairing_state
from .util import normalize_mac

if TYPE_CHECKING:
    from .coordinator import MadokaCoordinator

_LOGGER = logging.getLogger(__name__)

# Everything in entry.data that describes the CONNECTION rather than the user's
# choices. HA's async_update_entry(data=...) REPLACES the mapping, so any step
# that rebuilds entry.data from scratch silently deletes these — and losing
# bonded_sources alone turns a thermostat reachable through four proxies into a
# single-path device, one rename at a time.
CONNECTION_STATE_KEYS = (
    CONF_PREFERRED_SOURCE,
    CONF_BONDED_SOURCES,
    CONF_PAIRING_STATE,
)


def _device_type_selector() -> SelectSelector:
    """Selector for the appliance type (thermostat vs ventilation/VAM)."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[DEVICE_TYPE_THERMOSTAT, DEVICE_TYPE_VENTILATION],
            translation_key="device_type",
            mode=SelectSelectorMode.LIST,
        )
    )


class FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    def _configured_addresses(self, exclude_entry_id: str | None = None) -> set[str]:
        """Normalized MACs of all existing entries, legacy shapes included."""
        addresses: set[str] = set()
        for entry in self._async_current_entries(include_ignore=True):
            if entry.entry_id == exclude_entry_id:
                continue
            macs = []
            if CONF_MAC in entry.data:
                macs.append(entry.data[CONF_MAC])
            macs.extend(entry.data.get(CONF_DEVICES, []))
            for mac in macs:
                if (normalized := normalize_mac(mac)) is not None:
                    addresses.add(normalized)
        return addresses

    def _user_schema(self) -> vol.Schema:
        """Schema for manual/assisted setup: discovered devices + free MAC entry."""
        configured = self._configured_addresses()
        options = [
            SelectOptionDict(
                value=service_info.address,
                label=f"{service_info.name} ({service_info.address})",
            )
            for service_info in async_discovered_service_info(
                self.hass, connectable=True
            )
            if MADOKA_SERVICE_UUID in service_info.service_uuids
            and service_info.address.upper() not in configured
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
                vol.Required(
                    CONF_DEVICE_TYPE, default=DEFAULT_DEVICE_TYPE
                ): _device_type_selector(),
            }
        )

    async def _create_entry(
        self,
        mac: str,
        friendly_name: str,
        device_type: str = DEFAULT_DEVICE_TYPE,
        preferred_source: str | None = None,
    ) -> ConfigFlowResult:
        """Register new entry."""
        title = friendly_name.strip() or f"{BRC1H_NAME_PREFIX} {mac}"
        data: dict[str, Any] = {
            CONF_MAC: mac,
            CONF_FRIENDLY_NAME: friendly_name.strip(),
            CONF_DEVICE_TYPE: device_type,
        }
        # Prime the sticky proxy with the path that just validated, so the
        # very first setup reconnects through the bonded proxy instead of
        # whichever proxy wins on RSSI. None means the validation went
        # through the local adapter — nothing useful to store.
        if preferred_source is not None:
            data[CONF_PREFERRED_SOURCE] = preferred_source
        return self.async_create_entry(title=title, data=data)

    @callback
    def _async_coordinator(
        self, entry: ConfigEntry, mac: str
    ) -> "MadokaCoordinator | None":
        """The live coordinator for this MAC, when there is one.

        runtime_data only exists while the entry is loaded, and the mapping is
        keyed by normalized MAC. A flow must work with no coordinator at all
        (an entry being set up, a legacy shape), so every caller treats None as
        "nothing to nudge" rather than an error.
        """
        coordinators = getattr(entry, "runtime_data", None)
        if isinstance(coordinators, dict):
            return coordinators.get(mac)
        return None

    async def _async_validate_device(self, mac: str) -> tuple[str | None, str | None]:
        """Try a full authenticated connect. Returns (error_key, connected_source).

        A successful validation disconnects right away (controller.stop in the
        finally block). The BRC1H stops advertising while connected and needs
        a moment to reappear, so the device may be briefly invisible when
        async_setup_entry runs its real connect right after entry creation.
        That is acceptable — the coordinator fails fast on "not advertising"
        and retries on the next poll, and the entry now loads degraded — so no
        sleep is added here.

        Runs the USER_INITIATED profile: a config flow is by definition
        attended, so the pairing budget is the human one (PAIRING_WINDOW_TIMEOUT
        inside VALIDATE_TIMEOUT = PAIRING_CONNECT_TIMEOUT), and the candidate
        list is unrestricted — a proxy that holds no bond yet is exactly the one
        this connect exists to bond with.
        """
        from pymadoka import ConnectionStatus, Controller, PairingRequiredError

        from .util import build_candidates

        def _candidates():
            # Total by contract, like the coordinator's (see __init__): a
            # raising callback drops pymadoka onto its single-path fallback,
            # which rescores every path on each of its three attempts and can
            # hop to a proxy this flow never chose. Reporting no path turns that
            # into a plain "cannot_connect" the attending user can retry.
            try:
                return build_candidates(self.hass, mac, None)
            except Exception:  # see above
                _LOGGER.exception("Could not build the candidate list for %s", mac)
                return []

        controller = Controller(
            mac,
            hass=self.hass,
            reconnect=False,
            candidates_callback=_candidates,
            pair_timeout=PAIRING_WINDOW_TIMEOUT,
        )
        try:
            await asyncio.wait_for(controller.start(), timeout=VALIDATE_TIMEOUT)
            # start() can return NORMALLY with status ABORTED: its connect
            # loop stamps ABORTED on unclassified bleak/proxy errors instead
            # of raising, so a bare return does not prove a live connection.
            if controller.connection.connection_status is not ConnectionStatus.CONNECTED:
                return "cannot_connect", None
            return None, controller.connection.connected_source
        except PairingRequiredError:
            return "pairing_failed", None
        except Exception:  # noqa: BLE001  (DeviceUnreachableError, TimeoutError, anything)
            return "cannot_connect", None
        finally:
            # Best effort: the test connection is being torn down, so nothing
            # this raises changes the flow's outcome. Capped so a wedged
            # disconnect cannot hang the config flow.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(controller.stop(), timeout=10)

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a thermostat discovered by Home Assistant's Bluetooth stack."""
        # A very weak advert is almost certainly a neighbour's device: don't
        # offer a discovery card the user can't complete. Consequence of the
        # abort: HA will not re-fire discovery for this address until it
        # fully disappears from the scanners or HA restarts, so no card
        # appears even if the signal later improves. Manual setup via
        # async_step_user deliberately skips this filter (escape hatch).
        if (
            discovery_info.rssi is not None
            and discovery_info.rssi < RSSI_DISCOVERY_FLOOR
        ):
            return self.async_abort(reason="weak_signal")
        address = discovery_info.address.upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        # Legacy entries (multi-MAC, or manually typed MACs) may not carry a
        # matching unique_id — check entry data too.
        if address in self._configured_addresses():
            return self.async_abort(reason="already_configured")
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered thermostat and pick a name."""
        assert self._discovery_info is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = self._discovery_info.address.upper()
            error_key, source = await self._async_validate_device(mac)
            if error_key is None:
                return await self._create_entry(
                    mac,
                    user_input.get(CONF_FRIENDLY_NAME, ""),
                    user_input.get(CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE),
                    preferred_source=source,
                )
            errors["base"] = error_key

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_FRIENDLY_NAME, default=""): str,
                    vol.Required(
                        CONF_DEVICE_TYPE, default=DEFAULT_DEVICE_TYPE
                    ): _device_type_selector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User initiated config flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = normalize_mac(user_input[CONF_MAC])
            if mac is None:
                errors[CONF_MAC] = "not_a_mac"
            else:
                # raise_on_progress=False: a manual user flow takes precedence
                # over a pending Bluetooth discovery flow for the same device
                # (the discovery flow is aborted when the entry is created).
                await self.async_set_unique_id(mac, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                if mac in self._configured_addresses():
                    return self.async_abort(reason="already_configured")
                error_key, source = await self._async_validate_device(mac)
                if error_key is None:
                    return await self._create_entry(
                        mac,
                        user_input.get(CONF_FRIENDLY_NAME, ""),
                        user_input.get(CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE),
                        preferred_source=source,
                    )
                errors["base"] = error_key

        return self.async_show_form(
            step_id="user", data_schema=self._user_schema(), errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start recovery from a PROVEN pairing refusal.

        Started by the coordinator (ConfigEntry.async_start_reauth) the moment
        a path explicitly rejects the authenticated bond. Unlike the Reconnect
        button this needs no entities and no dashboard: HA puts a Fix button on
        the entry itself, which is the only affordance that still works when
        everything else about the device is broken.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to stand at the thermostat, then pair for real."""
        entry = self._get_reauth_entry()
        # Legacy multi-MAC entries have no single device to re-pair.
        if CONF_MAC not in entry.data:
            return self.async_abort(reason="reconfigure_legacy")
        mac = normalize_mac(entry.data[CONF_MAC]) or entry.data[CONF_MAC]
        errors: dict[str, str] = {}

        if user_input is not None:
            # Submitting this form means the user is standing at the
            # thermostat. Lift the retry brake BEFORE the attempt: the
            # evidence that armed it was gathered while nobody was touching
            # the device, and leaving it on means that if this attempt fails
            # the next one is up to TIMEOUT_BACKOFF_INTERVAL_S away — a
            # recovery action that visibly does nothing for a quarter of an
            # hour (field report 2026-07-26).
            coordinator = self._async_coordinator(entry, mac)
            if coordinator is not None:
                coordinator.async_clear_backoff()
            error_key, source = await self._async_validate_device(mac)
            if error_key is None:
                # The pairing just succeeded, so every verdict recorded against
                # this device is now false: drop the quarantine, the timeout
                # streak and the failure count, in memory and in the entry,
                # before the reload below rehydrates from it.
                async_forget_pairing_state(self.hass, entry, mac)
                data = {**entry.data}
                if source is not None:
                    # This proxy demonstrably holds a bond now: record it, so
                    # the automatic profile is allowed to use it.
                    data[CONF_PREFERRED_SOURCE] = source
                    bonded = list(data.get(CONF_BONDED_SOURCES, []))
                    if source not in bonded:
                        bonded.append(source)
                    data[CONF_BONDED_SOURCES] = bonded
                return self.async_update_reload_and_abort(
                    entry, data=data, reason="reauth_successful"
                )
            # Unlike the fire-and-forget Reconnect button, a failure here is
            # reported to the person who is standing at the thermostat.
            errors["base"] = error_key
            if coordinator is not None:
                # This flow's own connect has already been torn down, so hand
                # the retry straight back to the coordinator: the user is
                # still there, and it now runs at the configured cadence.
                self.hass.async_create_task(coordinator.async_request_refresh())

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the MAC and/or friendly name without delete + re-add.

        A rename alone needs no device round-trip; a MAC change is validated
        with a full authenticated connect, exactly like initial setup, and
        replaces the stored sticky proxy (the old bond belongs to the old
        device).
        """
        entry = self._get_reconfigure_entry()
        # Legacy multi-MAC entries have no single identity to rewrite;
        # reconfiguring one would silently drop its other thermostats.
        if CONF_MAC not in entry.data:
            return self.async_abort(reason="reconfigure_legacy")
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = normalize_mac(user_input[CONF_MAC])
            if mac is None:
                errors[CONF_MAC] = "not_a_mac"
            else:
                current_mac = normalize_mac(entry.data[CONF_MAC])
                mac_changed = mac != current_mac
                if mac_changed and mac in self._configured_addresses(
                    exclude_entry_id=entry.entry_id
                ):
                    return self.async_abort(reason="already_configured")
                error_key: str | None = None
                source: str | None = None
                if mac_changed:
                    error_key, source = await self._async_validate_device(mac)
                if error_key is None:
                    friendly = user_input.get(CONF_FRIENDLY_NAME, "").strip()
                    data: dict[str, Any] = {
                        CONF_MAC: mac,
                        CONF_FRIENDLY_NAME: friendly,
                        # Appliance type is a physical trait the reconfigure
                        # form can't edit, and entry.data is replaced wholesale
                        # here, so carry it through or a VAM silently reverts to
                        # a thermostat on a mere rename.
                        CONF_DEVICE_TYPE: entry.data.get(
                            CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE
                        ),
                    }
                    if mac_changed:
                        # A different thermostat: every bond, sticky proxy and
                        # verdict on record belongs to the old one and must NOT
                        # be carried over. The validation above just
                        # authenticated, so that path is the new device's first
                        # known bond.
                        if source is not None:
                            data[CONF_PREFERRED_SOURCE] = source
                            data[CONF_BONDED_SOURCES] = [source]
                    else:
                        # Same thermostat, so the connection state is still
                        # true. Carrying it through is not cosmetic: entry.data
                        # is replaced wholesale here, and dropping
                        # bonded_sources used to reduce a four-proxy device to
                        # its single preferred proxy on a mere rename.
                        for key in CONNECTION_STATE_KEYS:
                            if key in entry.data:
                                data[key] = entry.data[key]
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=mac,
                        title=friendly or f"{BRC1H_NAME_PREFIX} {mac}",
                        data=data,
                    )
                errors["base"] = error_key

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MAC, default=entry.data.get(CONF_MAC, "")
                    ): str,
                    vol.Optional(
                        CONF_FRIENDLY_NAME,
                        default=entry.data.get(CONF_FRIENDLY_NAME, ""),
                    ): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "OptionsFlowHandler":
        """Return the options flow handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow (poll interval)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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

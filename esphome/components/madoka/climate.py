import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import (
    binary_sensor,
    ble_client,
    button,
    climate,
    number,
    sensor,
    text_sensor,
)
from esphome.const import (
    CONF_ID,
    DEVICE_CLASS_PROBLEM,
    DEVICE_CLASS_TEMPERATURE,
    STATE_CLASS_MEASUREMENT,
    UNIT_CELSIUS,
)

CODEOWNERS = ["@Petapton"]
DEPENDENCIES = ["ble_client"]
AUTO_LOAD = ["binary_sensor", "button", "madoka_base", "number", "sensor", "text_sensor"]

CONF_DUAL_SETPOINT = "dual_setpoint"
CONF_OUTDOOR_TEMPERATURE = "outdoor_temperature"
CONF_CLEAN_FILTER = "clean_filter"
CONF_FIRMWARE_VERSION = "firmware_version"
CONF_EYE_BRIGHTNESS = "eye_brightness"
CONF_RESET_FILTER = "reset_filter"

# Declared here rather than imported from the madoka_base component: an
# external component that is not itself listed in `external_components:`
# cannot be imported by name. This is type metadata for codegen only --
# the shared implementation lives in madoka_base/madoka_base.h.
madoka_base_ns = cg.esphome_ns.namespace("madoka_base")
MadokaBase = madoka_base_ns.class_(
    "MadokaBase", climate.Climate, ble_client.BLEClientNode, cg.PollingComponent
)
madoka_ns = cg.esphome_ns.namespace("madoka")
Madoka = madoka_ns.class_("Madoka", MadokaBase)
MadokaEyeBrightnessNumber = madoka_ns.class_(
    "MadokaEyeBrightnessNumber", number.Number, cg.Parented.template(Madoka)
)
MadokaResetFilterButton = madoka_ns.class_(
    "MadokaResetFilterButton", button.Button, cg.Parented.template(Madoka)
)

CONFIG_SCHEMA = (
    climate.climate_schema(Madoka)
    .extend(ble_client.BLE_CLIENT_SCHEMA)
    .extend(cv.polling_component_schema("10s"))
    .extend(
        {
            # ESPHome advertises climate traits once, when the entity is
            # listed, so the dual setpoint cannot follow the thermostat's
            # range mode at runtime the way the HA integration does. It is a
            # build-time choice; default off, matching a stock BRC1H.
            cv.Optional(CONF_DUAL_SETPOINT, default=False): cv.boolean,
            cv.Optional(CONF_OUTDOOR_TEMPERATURE): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                accuracy_decimals=0,
                device_class=DEVICE_CLASS_TEMPERATURE,
                state_class=STATE_CLASS_MEASUREMENT,
            ),
            cv.Optional(CONF_CLEAN_FILTER): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_PROBLEM,
                icon="mdi:air-filter",
            ),
            cv.Optional(CONF_FIRMWARE_VERSION): text_sensor.text_sensor_schema(
                icon="mdi:chip",
            ),
            cv.Optional(CONF_EYE_BRIGHTNESS): number.number_schema(
                MadokaEyeBrightnessNumber,
                icon="mdi:brightness-6",
            ),
            cv.Optional(CONF_RESET_FILTER): button.button_schema(
                MadokaResetFilterButton,
                icon="mdi:air-filter",
            ),
        }
    )
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await climate.register_climate(var, config)
    await ble_client.register_ble_node(var, config)

    cg.add(var.set_dual_setpoint(config[CONF_DUAL_SETPOINT]))

    if conf := config.get(CONF_OUTDOOR_TEMPERATURE):
        outdoor_sensor = await sensor.new_sensor(conf)
        cg.add(var.set_outdoor_temperature_sensor(outdoor_sensor))

    if conf := config.get(CONF_CLEAN_FILTER):
        clean_filter_sensor = await binary_sensor.new_binary_sensor(conf)
        cg.add(var.set_clean_filter_binary_sensor(clean_filter_sensor))

    if conf := config.get(CONF_FIRMWARE_VERSION):
        firmware_sensor = await text_sensor.new_text_sensor(conf)
        cg.add(var.set_firmware_version_text_sensor(firmware_sensor))

    if conf := config.get(CONF_EYE_BRIGHTNESS):
        brightness_number = await number.new_number(conf, min_value=0, max_value=19, step=1)
        cg.add(brightness_number.set_parent(var))
        cg.add(var.set_eye_brightness_number(brightness_number))

    if conf := config.get(CONF_RESET_FILTER):
        reset_button = await button.new_button(conf)
        cg.add(reset_button.set_parent(var))
        cg.add(var.set_reset_filter_button(reset_button))

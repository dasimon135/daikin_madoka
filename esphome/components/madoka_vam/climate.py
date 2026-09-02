import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import (
    binary_sensor,
    ble_client,
    button,
    climate,
    number,
    text_sensor,
)
from esphome.const import CONF_ID, DEVICE_CLASS_PROBLEM

CODEOWNERS = ["@Frank802"]
DEPENDENCIES = ["ble_client"]
AUTO_LOAD = ["binary_sensor", "button", "number", "text_sensor"]

CONF_CLEAN_FILTER = "clean_filter"
CONF_FIRMWARE_VERSION = "firmware_version"
CONF_EYE_BRIGHTNESS = "eye_brightness"
CONF_RESET_FILTER = "reset_filter"

madoka_vam_ns = cg.esphome_ns.namespace("madoka_vam")
MadokaVam = madoka_vam_ns.class_(
    "MadokaVam", climate.Climate, ble_client.BLEClientNode, cg.PollingComponent
)
MadokaEyeBrightnessNumber = madoka_vam_ns.class_(
    "MadokaEyeBrightnessNumber", number.Number, cg.Parented.template(MadokaVam)
)
MadokaResetFilterButton = madoka_vam_ns.class_(
    "MadokaResetFilterButton", button.Button, cg.Parented.template(MadokaVam)
)

CONFIG_SCHEMA = (
    climate.climate_schema(MadokaVam)
    .extend(ble_client.BLE_CLIENT_SCHEMA)
    .extend(cv.polling_component_schema("10s"))
    .extend(
        {
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

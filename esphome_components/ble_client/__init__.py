from esphome import automationfrom esphome import automation

from esphome.automation import maybe_simple_idfrom esphome.automation import maybe_simple_id

import esphome.codegen as cgimport esphome.codegen as cg

from esphome.components import esp32_ble, esp32_ble_client, esp32_ble_trackerfrom esphome.components import esp32_ble, esp32_ble_client, esp32_ble_tracker

from esphome.components.esp32_ble import BTLoggersfrom esphome.components.esp32_ble import BTLoggers

import esphome.config_validation as cvimport esphome.config_validation as cv

from esphome.const import (from esphome.const import (

    CONF_CHARACTERISTIC_UUID,    CONF_CHARACTERISTIC_UUID,

    CONF_ID,    CONF_ID,

    CONF_MAC_ADDRESS,    CONF_MAC_ADDRESS,

    CONF_NAME,    CONF_NAME,

    CONF_ON_CONNECT,    CONF_ON_CONNECT,

    CONF_ON_DISCONNECT,    CONF_ON_DISCONNECT,

    CONF_SERVICE_UUID,    CONF_SERVICE_UUID,

    CONF_TRIGGER_ID,    CONF_TRIGGER_ID,

    CONF_VALUE,    CONF_VALUE,

))



AUTO_LOAD = ["esp32_ble_client"]AUTO_LOAD = ["esp32_ble_client"]

CODEOWNERS = ["@buxtronix", "@clydebarrow"]CODEOWNERS = ["@buxtronix", "@clydebarrow"]

DEPENDENCIES = ["esp32_ble_tracker"]DEPENDENCIES = ["esp32_ble_tracker"]



ble_client_ns = cg.esphome_ns.namespace("ble_client")ble_client_ns = cg.esphome_ns.namespace("ble_client")

BLEClient = ble_client_ns.class_("BLEClient", esp32_ble_client.BLEClientBase)BLEClient = ble_client_ns.class_("BLEClient", esp32_ble_client.BLEClientBase)

BLEClientNode = ble_client_ns.class_("BLEClientNode")BLEClientNode = ble_client_ns.class_("BLEClientNode")

BLEClientNodeConstRef = BLEClientNode.operator("ref").operator("const")BLEClientNodeConstRef = BLEClientNode.operator("ref").operator("const")

# Triggers# Triggers

BLEClientConnectTrigger = ble_client_ns.class_(BLEClientConnectTrigger = ble_client_ns.class_(

    "BLEClientConnectTrigger", automation.Trigger.template(BLEClientNodeConstRef)    "BLEClientConnectTrigger", automation.Trigger.template(BLEClientNodeConstRef)

))

BLEClientDisconnectTrigger = ble_client_ns.class_(BLEClientDisconnectTrigger = ble_client_ns.class_(

    "BLEClientDisconnectTrigger", automation.Trigger.template(BLEClientNodeConstRef)    "BLEClientDisconnectTrigger", automation.Trigger.template(BLEClientNodeConstRef)

))

BLEClientPasskeyRequestTrigger = ble_client_ns.class_(BLEClientPasskeyRequestTrigger = ble_client_ns.class_(

    "BLEClientPasskeyRequestTrigger", automation.Trigger.template(BLEClientNodeConstRef)    "BLEClientPasskeyRequestTrigger", automation.Trigger.template(BLEClientNodeConstRef)

))

BLEClientPasskeyNotificationTrigger = ble_client_ns.class_(BLEClientPasskeyNotificationTrigger = ble_client_ns.class_(

    "BLEClientPasskeyNotificationTrigger",    "BLEClientPasskeyNotificationTrigger",

    automation.Trigger.template(BLEClientNodeConstRef, cg.uint32),    automation.Trigger.template(BLEClientNodeConstRef, cg.uint32),

))

BLEClientNumericComparisonRequestTrigger = ble_client_ns.class_(BLEClientNumericComparisonRequestTrigger = ble_client_ns.class_(

    "BLEClientNumericComparisonRequestTrigger",    "BLEClientNumericComparisonRequestTrigger",

    automation.Trigger.template(BLEClientNodeConstRef, cg.uint32),    automation.Trigger.template(BLEClientNodeConstRef, cg.uint32),

))



# Actions# Actions

BLEWriteAction = ble_client_ns.class_("BLEClientWriteAction", automation.Action)BLEWriteAction = ble_client_ns.class_("BLEClientWriteAction", automation.Action)

BLEConnectAction = ble_client_ns.class_("BLEClientConnectAction", automation.Action)BLEConnectAction = ble_client_ns.class_("BLEClientConnectAction", automation.Action)

BLEDisconnectAction = ble_client_ns.class_(BLEDisconnectAction = ble_client_ns.class_(

    "BLEClientDisconnectAction", automation.Action    "BLEClientDisconnectAction", automation.Action

))

BLEPasskeyReplyAction = ble_client_ns.class_(BLEPasskeyReplyAction = ble_client_ns.class_(

    "BLEClientPasskeyReplyAction", automation.Action    "BLEClientPasskeyReplyAction", automation.Action

))

BLENumericComparisonReplyAction = ble_client_ns.class_(BLENumericComparisonReplyAction = ble_client_ns.class_(

    "BLEClientNumericComparisonReplyAction", automation.Action    "BLEClientNumericComparisonReplyAction", automation.Action

))

BLERemoveBondAction = ble_client_ns.class_(BLERemoveBondAction = ble_client_ns.class_(

    "BLEClientRemoveBondAction", automation.Action    "BLEClientRemoveBondAction", automation.Action

))



CONF_PASSKEY = "passkey"CONF_PASSKEY = "passkey"

CONF_ACCEPT = "accept"CONF_ACCEPT = "accept"

CONF_ON_PASSKEY_REQUEST = "on_passkey_request"CONF_ON_PASSKEY_REQUEST = "on_passkey_request"

CONF_ON_PASSKEY_NOTIFICATION = "on_passkey_notification"CONF_ON_PASSKEY_NOTIFICATION = "on_passkey_notification"

CONF_ON_NUMERIC_COMPARISON_REQUEST = "on_numeric_comparison_request"CONF_ON_NUMERIC_COMPARISON_REQUEST = "on_numeric_comparison_request"

CONF_AUTO_CONNECT = "auto_connect"CONF_AUTO_CONNECT = "auto_connect"



MULTI_CONF = TrueMULTI_CONF = True



# Wrapper pour gérer consume_connection_slots qui peut être absent dans certaines versionsCONFIG_SCHEMA = cv.All(

def safe_consume_connection_slots(slots, component_name):    cv.Schema(

    """Wrapper pour consume_connection_slots compatible avec toutes les versions ESPHome."""        {

    if hasattr(esp32_ble_tracker, 'consume_connection_slots'):            cv.GenerateID(): cv.declare_id(BLEClient),

        return esp32_ble_tracker.consume_connection_slots(slots, component_name)            cv.Required(CONF_MAC_ADDRESS): cv.mac_address,

    else:            cv.Optional(CONF_NAME): cv.string,

        # Pour ESPHome 2025.10.0+, retourner une fonction qui ne fait rien            cv.Optional(CONF_AUTO_CONNECT, default=True): cv.boolean,

        return lambda config: config            cv.Optional(CONF_ON_CONNECT): automation.validate_automation(

                {

CONFIG_SCHEMA = cv.All(                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(

    cv.Schema(                        BLEClientConnectTrigger

        {                    ),

            cv.GenerateID(): cv.declare_id(BLEClient),                }

            cv.Required(CONF_MAC_ADDRESS): cv.mac_address,            ),

            cv.Optional(CONF_NAME): cv.string,            cv.Optional(CONF_ON_DISCONNECT): automation.validate_automation(

            cv.Optional(CONF_AUTO_CONNECT, default=True): cv.boolean,                {

            cv.Optional(CONF_ON_CONNECT): automation.validate_automation(                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(

                {                        BLEClientDisconnectTrigger

                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(                    ),

                        BLEClientConnectTrigger                }

                    ),            ),

                }            cv.Optional(CONF_ON_PASSKEY_REQUEST): automation.validate_automation(

            ),                {

            cv.Optional(CONF_ON_DISCONNECT): automation.validate_automation(                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(

                {                        BLEClientPasskeyRequestTrigger

                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(                    ),

                        BLEClientDisconnectTrigger                }

                    ),            ),

                }            cv.Optional(CONF_ON_PASSKEY_NOTIFICATION): automation.validate_automation(

            ),                {

            cv.Optional(CONF_ON_PASSKEY_REQUEST): automation.validate_automation(                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(

                {                        BLEClientPasskeyNotificationTrigger

                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(                    ),

                        BLEClientPasskeyRequestTrigger                }

                    ),            ),

                }            cv.Optional(

            ),                CONF_ON_NUMERIC_COMPARISON_REQUEST

            cv.Optional(CONF_ON_PASSKEY_NOTIFICATION): automation.validate_automation(            ): automation.validate_automation(

                {                {

                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(

                        BLEClientPasskeyNotificationTrigger                        BLEClientNumericComparisonRequestTrigger

                    ),                    ),

                }                }

            ),            ),

            cv.Optional(        }

                CONF_ON_NUMERIC_COMPARISON_REQUEST    )

            ): automation.validate_automation(    .extend(cv.COMPONENT_SCHEMA)

                {    .extend(esp32_ble_tracker.ESP_BLE_DEVICE_SCHEMA),

                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(    esp32_ble_tracker.consume_connection_slots(1, "ble_client"),

                        BLEClientNumericComparisonRequestTrigger)

                    ),

                }CONF_BLE_CLIENT_ID = "ble_client_id"

            ),

        }BLE_CLIENT_SCHEMA = cv.Schema(

    )    {

    .extend(cv.COMPONENT_SCHEMA)        cv.GenerateID(CONF_BLE_CLIENT_ID): cv.use_id(BLEClient),

    .extend(esp32_ble_tracker.ESP_BLE_DEVICE_SCHEMA),    }

    safe_consume_connection_slots(1, "ble_client"),)

)



CONF_BLE_CLIENT_ID = "ble_client_id"async def register_ble_node(var, config):

    parent = await cg.get_variable(config[CONF_BLE_CLIENT_ID])

BLE_CLIENT_SCHEMA = cv.Schema(    cg.add(parent.register_ble_node(var))

    {

        cv.GenerateID(CONF_BLE_CLIENT_ID): cv.use_id(BLEClient),

    }BLE_WRITE_ACTION_SCHEMA = cv.Schema(

)    {

        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),

        cv.Required(CONF_SERVICE_UUID): esp32_ble_tracker.bt_uuid,

async def register_ble_node(var, config):        cv.Required(CONF_CHARACTERISTIC_UUID): esp32_ble_tracker.bt_uuid,

    parent = await cg.get_variable(config[CONF_BLE_CLIENT_ID])        cv.Required(CONF_VALUE): cv.templatable(cv.ensure_list(cv.hex_uint8_t)),

    cg.add(parent.register_ble_node(var))    }

)



BLE_WRITE_ACTION_SCHEMA = cv.Schema(BLE_CONNECT_ACTION_SCHEMA = maybe_simple_id(

    {    {

        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),

        cv.Required(CONF_SERVICE_UUID): esp32_ble_tracker.bt_uuid,    }

        cv.Required(CONF_CHARACTERISTIC_UUID): esp32_ble_tracker.bt_uuid,)

        cv.Required(CONF_VALUE): cv.templatable(cv.ensure_list(cv.hex_uint8_t)),

    }BLE_NUMERIC_COMPARISON_REPLY_ACTION_SCHEMA = cv.Schema(

)    {

        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),

BLE_CONNECT_ACTION_SCHEMA = maybe_simple_id(        cv.Required(CONF_ACCEPT): cv.templatable(cv.boolean),

    {    }

        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),)

    }

)BLE_PASSKEY_REPLY_ACTION_SCHEMA = cv.Schema(

    {

BLE_NUMERIC_COMPARISON_REPLY_ACTION_SCHEMA = cv.Schema(        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),

    {        cv.Required(CONF_PASSKEY): cv.templatable(cv.int_range(min=0, max=999999)),

        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),    }

        cv.Required(CONF_ACCEPT): cv.templatable(cv.boolean),)

    }

)

BLE_REMOVE_BOND_ACTION_SCHEMA = cv.Schema(

BLE_PASSKEY_REPLY_ACTION_SCHEMA = cv.Schema(    {

    {        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),

        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),    }

        cv.Required(CONF_PASSKEY): cv.templatable(cv.int_range(min=0, max=999999)),)

    }

)

@automation.register_action(

    "ble_client.disconnect", BLEDisconnectAction, BLE_CONNECT_ACTION_SCHEMA

BLE_REMOVE_BOND_ACTION_SCHEMA = cv.Schema()

    {async def ble_disconnect_to_code(config, action_id, template_arg, args):

        cv.GenerateID(CONF_ID): cv.use_id(BLEClient),    parent = await cg.get_variable(config[CONF_ID])

    }    var = cg.new_Pvariable(action_id, template_arg, parent)

)    return var





@automation.register_action(@automation.register_action(

    "ble_client.disconnect", BLEDisconnectAction, BLE_CONNECT_ACTION_SCHEMA    "ble_client.connect", BLEConnectAction, BLE_CONNECT_ACTION_SCHEMA

))

async def ble_disconnect_to_code(config, action_id, template_arg, args):async def ble_connect_to_code(config, action_id, template_arg, args):

    parent = await cg.get_variable(config[CONF_ID])    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)    var = cg.new_Pvariable(action_id, template_arg, parent)

    return var    return var





@automation.register_action(@automation.register_action(

    "ble_client.connect", BLEConnectAction, BLE_CONNECT_ACTION_SCHEMA    "ble_client.ble_write", BLEWriteAction, BLE_WRITE_ACTION_SCHEMA

))

async def ble_connect_to_code(config, action_id, template_arg, args):async def ble_write_to_code(config, action_id, template_arg, args):

    parent = await cg.get_variable(config[CONF_ID])    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)    var = cg.new_Pvariable(action_id, template_arg, parent)

    return var

    value = config[CONF_VALUE]

    if cg.is_template(value):

@automation.register_action(        templ = await cg.templatable(value, args, cg.std_vector.template(cg.uint8))

    "ble_client.ble_write", BLEWriteAction, BLE_WRITE_ACTION_SCHEMA        cg.add(var.set_value_template(templ))

)    else:

async def ble_write_to_code(config, action_id, template_arg, args):        cg.add(var.set_value_simple(value))

    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)    if len(config[CONF_SERVICE_UUID]) == len(esp32_ble_tracker.bt_uuid16_format):

        cg.add(

    value = config[CONF_VALUE]            var.set_service_uuid16(esp32_ble_tracker.as_hex(config[CONF_SERVICE_UUID]))

    if cg.is_template(value):        )

        templ = await cg.templatable(value, args, cg.std_vector.template(cg.uint8))    elif len(config[CONF_SERVICE_UUID]) == len(esp32_ble_tracker.bt_uuid32_format):

        cg.add(var.set_value_template(templ))        cg.add(

    else:            var.set_service_uuid32(esp32_ble_tracker.as_hex(config[CONF_SERVICE_UUID]))

        cg.add(var.set_value_simple(value))        )

    elif len(config[CONF_SERVICE_UUID]) == len(esp32_ble_tracker.bt_uuid128_format):

    if len(config[CONF_SERVICE_UUID]) == len(esp32_ble_tracker.bt_uuid16_format):        uuid128 = esp32_ble_tracker.as_reversed_hex_array(config[CONF_SERVICE_UUID])

        cg.add(        cg.add(var.set_service_uuid128(uuid128))

            var.set_service_uuid16(esp32_ble_tracker.as_hex(config[CONF_SERVICE_UUID]))

        )    if len(config[CONF_CHARACTERISTIC_UUID]) == len(esp32_ble_tracker.bt_uuid16_format):

    elif len(config[CONF_SERVICE_UUID]) == len(esp32_ble_tracker.bt_uuid32_format):        cg.add(

        cg.add(            var.set_char_uuid16(

            var.set_service_uuid32(esp32_ble_tracker.as_hex(config[CONF_SERVICE_UUID]))                esp32_ble_tracker.as_hex(config[CONF_CHARACTERISTIC_UUID])

        )            )

    elif len(config[CONF_SERVICE_UUID]) == len(esp32_ble_tracker.bt_uuid128_format):        )

        uuid128 = esp32_ble_tracker.as_reversed_hex_array(config[CONF_SERVICE_UUID])    elif len(config[CONF_CHARACTERISTIC_UUID]) == len(

        cg.add(var.set_service_uuid128(uuid128))        esp32_ble_tracker.bt_uuid32_format

    ):

    if len(config[CONF_CHARACTERISTIC_UUID]) == len(esp32_ble_tracker.bt_uuid16_format):        cg.add(

        cg.add(            var.set_char_uuid32(

            var.set_char_uuid16(                esp32_ble_tracker.as_hex(config[CONF_CHARACTERISTIC_UUID])

                esp32_ble_tracker.as_hex(config[CONF_CHARACTERISTIC_UUID])            )

            )        )

        )    elif len(config[CONF_CHARACTERISTIC_UUID]) == len(

    elif len(config[CONF_CHARACTERISTIC_UUID]) == len(        esp32_ble_tracker.bt_uuid128_format

        esp32_ble_tracker.bt_uuid32_format    ):

    ):        uuid128 = esp32_ble_tracker.as_reversed_hex_array(

        cg.add(            config[CONF_CHARACTERISTIC_UUID]

            var.set_char_uuid32(        )

                esp32_ble_tracker.as_hex(config[CONF_CHARACTERISTIC_UUID])        cg.add(var.set_char_uuid128(uuid128))

            )

        )    return var

    elif len(config[CONF_CHARACTERISTIC_UUID]) == len(

        esp32_ble_tracker.bt_uuid128_format

    ):@automation.register_action(

        uuid128 = esp32_ble_tracker.as_reversed_hex_array(    "ble_client.numeric_comparison_reply",

            config[CONF_CHARACTERISTIC_UUID]    BLENumericComparisonReplyAction,

        )    BLE_NUMERIC_COMPARISON_REPLY_ACTION_SCHEMA,

        cg.add(var.set_char_uuid128(uuid128)))

async def numeric_comparison_reply_to_code(config, action_id, template_arg, args):

    return var    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)



@automation.register_action(    accept = config[CONF_ACCEPT]

    "ble_client.numeric_comparison_reply",    if cg.is_template(accept):

    BLENumericComparisonReplyAction,        templ = await cg.templatable(accept, args, cg.bool_)

    BLE_NUMERIC_COMPARISON_REPLY_ACTION_SCHEMA,        cg.add(var.set_value_template(templ))

)    else:

async def numeric_comparison_reply_to_code(config, action_id, template_arg, args):        cg.add(var.set_value_simple(accept))

    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)    return var



    accept = config[CONF_ACCEPT]

    if cg.is_template(accept):@automation.register_action(

        templ = await cg.templatable(accept, args, cg.bool_)    "ble_client.passkey_reply", BLEPasskeyReplyAction, BLE_PASSKEY_REPLY_ACTION_SCHEMA

        cg.add(var.set_value_template(templ)))

    else:async def passkey_reply_to_code(config, action_id, template_arg, args):

        cg.add(var.set_value_simple(accept))    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)

    return var

    passkey = config[CONF_PASSKEY]

    if cg.is_template(passkey):

@automation.register_action(        templ = await cg.templatable(passkey, args, cg.uint32)

    "ble_client.passkey_reply", BLEPasskeyReplyAction, BLE_PASSKEY_REPLY_ACTION_SCHEMA        cg.add(var.set_value_template(templ))

)    else:

async def passkey_reply_to_code(config, action_id, template_arg, args):        cg.add(var.set_value_simple(passkey))

    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)    return var



    passkey = config[CONF_PASSKEY]

    if cg.is_template(passkey):@automation.register_action(

        templ = await cg.templatable(passkey, args, cg.uint32)    "ble_client.remove_bond",

        cg.add(var.set_value_template(templ))    BLERemoveBondAction,

    else:    BLE_REMOVE_BOND_ACTION_SCHEMA,

        cg.add(var.set_value_simple(passkey)))

async def remove_bond_to_code(config, action_id, template_arg, args):

    return var    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)



@automation.register_action(    return var

    "ble_client.remove_bond",

    BLERemoveBondAction,

    BLE_REMOVE_BOND_ACTION_SCHEMA,async def to_code(config):

)    # Register the loggers this component needs

async def remove_bond_to_code(config, action_id, template_arg, args):    esp32_ble.register_bt_logger(BTLoggers.GATT, BTLoggers.SMP)

    parent = await cg.get_variable(config[CONF_ID])

    var = cg.new_Pvariable(action_id, template_arg, parent)    var = cg.new_Pvariable(config[CONF_ID])

    await cg.register_component(var, config)

    return var    await esp32_ble_tracker.register_client(var, config)

    cg.add(var.set_address(config[CONF_MAC_ADDRESS].as_hex))

    cg.add(var.set_auto_connect(config[CONF_AUTO_CONNECT]))

async def to_code(config):    for conf in config.get(CONF_ON_CONNECT, []):

    # Register the loggers this component needs        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)

    esp32_ble.register_bt_logger(BTLoggers.GATT, BTLoggers.SMP)        await automation.build_automation(trigger, [], conf)

    for conf in config.get(CONF_ON_DISCONNECT, []):

    var = cg.new_Pvariable(config[CONF_ID])        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)

    await cg.register_component(var, config)        await automation.build_automation(trigger, [], conf)

    await esp32_ble_tracker.register_client(var, config)    for conf in config.get(CONF_ON_PASSKEY_REQUEST, []):

    cg.add(var.set_address(config[CONF_MAC_ADDRESS].as_hex))        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)

    cg.add(var.set_auto_connect(config[CONF_AUTO_CONNECT]))        await automation.build_automation(trigger, [], conf)

    for conf in config.get(CONF_ON_CONNECT, []):    for conf in config.get(CONF_ON_PASSKEY_NOTIFICATION, []):

        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)

        await automation.build_automation(trigger, [], conf)        await automation.build_automation(trigger, [(cg.uint32, "passkey")], conf)

    for conf in config.get(CONF_ON_DISCONNECT, []):    for conf in config.get(CONF_ON_NUMERIC_COMPARISON_REQUEST, []):

        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)

        await automation.build_automation(trigger, [], conf)        await automation.build_automation(trigger, [(cg.uint32, "passkey")], conf)

    for conf in config.get(CONF_ON_PASSKEY_REQUEST, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [], conf)
    for conf in config.get(CONF_ON_PASSKEY_NOTIFICATION, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(cg.uint32, "passkey")], conf)
    for conf in config.get(CONF_ON_NUMERIC_COMPARISON_REQUEST, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(cg.uint32, "passkey")], conf)

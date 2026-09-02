"""Shared BLE transport for the Daikin Madoka components.

Code-only component: it declares no YAML of its own and is never named in a
configuration. `madoka` and `madoka_vam` pull it in through AUTO_LOAD so its
sources are copied into the build alongside theirs, and import `MadokaBase`
from here so the class hierarchy is declared once.
"""

import esphome.codegen as cg
from esphome.components import ble_client, climate

CODEOWNERS = ["@dasimon135"]

madoka_base_ns = cg.esphome_ns.namespace("madoka_base")
MadokaBase = madoka_base_ns.class_(
    "MadokaBase", climate.Climate, ble_client.BLEClientNode, cg.PollingComponent
)

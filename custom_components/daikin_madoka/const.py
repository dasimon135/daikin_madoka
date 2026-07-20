"""Daikin Madoka consts."""

DOMAIN = "daikin_madoka"
CONF_MAC = "address"
CONF_FRIENDLY_NAME = "friendly_name"
# Source (proxy) MAC of the path that last authenticated successfully; the
# candidates list is ordered sticky-first so reconnects go back to the bonded
# proxy instead of whichever proxy wins on RSSI.
CONF_PREFERRED_SOURCE = "preferred_source"
# Every proxy that has completed an authenticated session with this device, so
# it is known to hold a bond. Automatic reconnects are restricted to these:
# connecting through an unbonded proxy starts a real numeric-comparison
# pairing, which no unattended retry can ever complete and which jams the
# BRC1H when repeated. Empty/absent means "not known yet" — treated as
# unrestricted so a fresh install can still find its first path.
CONF_BONDED_SOURCES = "bonded_sources"

# Pairing budget while a user-initiated pairing window is open. Long enough to
# walk to the thermostat, compare the 6-digit code and accept; the default 8s
# used by automatic reconnects cannot accommodate a human.
PAIRING_WINDOW_TIMEOUT = 60.0

BRC1H_NAME_PREFIX = "BRC1H"

# Advertised by the BRC1H (local_name is just "Daikin", so the service UUID is
# the reliable discovery signal). Must stay lowercase for HA matchers.
MADOKA_SERVICE_UUID = "2141e110-213a-11e6-b67b-9e71128cae77"

MIN_TEMP = 16
MAX_TEMP = 32

DEFAULT_SCAN_INTERVAL = 60
# Failed polls masked by serving the last good data instead of raising: a
# one-off BLE micro-drop should not punch holes in graphs or flip entities
# unavailable. Kept well below UNREACHABLE_THRESHOLD so a real outage still
# surfaces quickly; pairing refusals are never masked.
STALE_GRACE = 2
# Must exceed the connect path's internal budget (establish_connection retries
# + pairing + settle), or reconnects get cancelled mid-handshake.
CONNECT_TIMEOUT = 30
# Discovery adverts below this RSSI are almost certainly a neighbour's BRC1H
# bleeding through a wall: don't offer a discovery card for a device the user
# can't actually pair with. Manual setup (async_step_user) is the escape
# hatch — it never filters on signal strength.
RSSI_DISCOVERY_FLOOR = -90
# Ceiling on the config-flow validation connect. Matches CONNECT_TIMEOUT's
# rationale: it must exceed the connect path's internal budget (candidate
# retries + pairing prompt + settle) or a slow-but-successful first pairing
# gets cut short and reported as cannot_connect.
VALIDATE_TIMEOUT = 30
# Hard ceiling on one full-feature poll (queries retry individually).
POLL_TIMEOUT = 45

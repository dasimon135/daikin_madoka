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
# Consecutive PROVEN pairing refusals attributed to one proxy before that proxy
# is dropped from CONF_BONDED_SOURCES. The list used to be append-only, so a
# reflashed, replaced or manually unpaired proxy stayed "bonded" forever and
# every reconnect kept retrying that dead path — feeding the very storm the
# restriction exists to prevent. Three, not one: attribution is imperfect (see
# MadokaCoordinator._async_evict_dead_bond) and forgetting a good bond costs a
# full re-pair with a human at the thermostat, so the evidence has to repeat.
BOND_EVICTION_FAILURES = 3
# Durable shadow of the per-MAC pairing verdict (suspended / backoff /
# timeout streak / consecutive failures / last pairing error), keyed by MAC.
# The live copy lives in hass.data so it survives a coordinator rebuild; this
# copy makes it survive an HA restart as well, so a diagnosis reached at 2am
# does not have to be re-derived — and, being entry data, it disappears with
# the entry, which keeps delete-and-re-add working as the last-resort escape
# hatch.
CONF_PAIRING_STATE = "pairing_state"

BRC1H_NAME_PREFIX = "BRC1H"

# Advertised by the BRC1H (local_name is just "Daikin", so the service UUID is
# the reliable discovery signal). Must stay lowercase for HA matchers.
MADOKA_SERVICE_UUID = "2141e110-213a-11e6-b67b-9e71128cae77"

MIN_TEMP = 16
MAX_TEMP = 32

DEFAULT_SCAN_INTERVAL = 60
# Madoka Assistant energy-consumption protocol. The counters are uint32 little
# endian values in tenths of a kWh and are available only after this privileged
# request on an authenticated connection.
ENERGY_CONSUMPTION_COMMAND = 0x0120
ENERGY_PRIVILEGE_COMMAND = 0x4112
ENERGY_PRIVILEGE_PARAMETER = 0xFE
ENERGY_SCAN_INTERVAL = 300
ENERGY_PARAMETERS = {
    "energy_today": 0x40,
    "energy_yesterday": 0x41,
    "energy_this_week": 0x42,
    "energy_last_week": 0x43,
    "energy_this_year": 0x44,
    "energy_last_year": 0x45,
}
# Failed polls masked by serving the last good data instead of raising: a
# one-off BLE micro-drop should not punch holes in graphs or flip entities
# unavailable. Kept well below UNREACHABLE_THRESHOLD so a real outage still
# surfaces quickly; pairing refusals are never masked.
STALE_GRACE = 2
# ---------------------------------------------------------------------------
# Connection budgets — THE INVARIANT, and it is a PER-ROUND one:
#
#     N * (CANDIDATE_CONNECT_OVERHEAD_S + pair_budget) < outer connect budget
#
# where N is the number of candidate paths the connect will try.
#
# Not a matter of taste. pymadoka wraps its own pair() in wait_for(pair_timeout),
# but it only counts a pairing ROUND after it has walked EVERY candidate
# (`every_path_failed_auth = auth_rejections + pair_timeouts == len(candidates)`
# in Connection._connect_via_ha). A round therefore costs N times one candidate's
# connect overhead plus its full pair budget — not one pair budget. If the outer
# wait cancels the attempt before the loop ends, no round is ever counted, no
# verdict (rejection vs timeout streak) can ever form, and the dead-bond
# quarantine silently stops working.
#
# Two regressions came out of getting this wrong, and they were NOT the same bug:
#   * v3.7.1 shipped BOOT_PAIR_TIMEOUT = 30 under CONNECT_TIMEOUT = 30, so the
#     inner timeout could not fire even with a single candidate.
#   * The follow-up stated the invariant PER ATTEMPT (22 < 30), which only holds
#     for N == 1. In a house with 3-4 proxies seeing each thermostat — the
#     ordinary case here — every attempt was cancelled at 30s with the round
#     counter still at 0, forever. No verdict, hence no backoff, hence a dead
#     bond polled at full cadence: ~80 SMP initiations/h against ~18 before.
#
# Hence the budgets below are CEILINGS, not the values actually used: the
# effective pair budget is shaped against the live candidate count by
# coordinator.connection_profile(), which also stretches the outer budget when
# the floor binds. Two profiles, selected by who initiated the attempt:
#
#   profile         inner pair ceiling           outer connect ceiling
#   AUTOMATIC       AUTOMATIC_PAIR_TIMEOUT 22    CONNECT_TIMEOUT          30
#   USER_INITIATED  PAIRING_WINDOW_TIMEOUT 60    PAIRING_CONNECT_TIMEOUT  90
# ---------------------------------------------------------------------------

# Must exceed the connect path's internal budget (establish_connection retries
# + pairing + settle), or reconnects get cancelled mid-handshake.
CONNECT_TIMEOUT = 30

# AUTOMATIC profile. No human is involved, so this budget is not about giving
# anyone time to confirm: it is about letting a *valid* bond finish encrypting
# through a congested ESPHome proxy after a restart, which the tight 8s
# library default (pymadoka DEFAULT_PAIR_TIMEOUT) mistakes for a timeout round.
# Wide enough for that, still under CONNECT_TIMEOUT so pymadoka's own timeout
# always fires first and the evidence is collected.
AUTOMATIC_PAIR_TIMEOUT = 22.0

# USER_INITIATED profile: a pairing window is open because the user pressed
# Reconnect and is standing at the thermostat. Long enough to walk over,
# compare the 6-digit code and accept.
PAIRING_WINDOW_TIMEOUT = 60.0
# ...which only becomes real if the outer budget leaves room for it. Under the
# ordinary CONNECT_TIMEOUT the 60s human budget was dead config: every attempt
# was cancelled at ~28s.
PAIRING_CONNECT_TIMEOUT = 90.0

# Everything a candidate costs BESIDES its pairing budget: establish_connection
# (max_attempts=1), the notify subscription and pymadoka's SETTLE_DELAY. ~2s on
# a local adapter, ~3s through a loaded ESPHome proxy. Over-estimating only
# tightens the shaped pair budget slightly; under-estimating puts the round back
# over the outer budget, which is the whole bug, so round up.
CANDIDATE_CONNECT_OVERHEAD_S = 3.0
# Floor under the shaped pair budget. pymadoka's own default is 8s and v3.7.1
# exists precisely because a VALID bond re-encrypting through a congested proxy
# needs more than a tight budget: shrinking below this to make a round fit would
# re-create the failure v3.7.1 fixed. When the floor binds, the outer budget is
# stretched instead — a longer attempt is affordable (the cadence brake keeps a
# stalling device to one attempt per TIMEOUT_BACKOFF_INTERVAL_S), a mis-timed
# one is not.
MIN_PAIR_TIMEOUT = 8.0
# Slack kept inside the outer budget for the parts of an attempt that are not
# per-candidate work: building the candidate list, the library's own
# classification after the loop, and scheduler jitter.
ROUND_HEADROOM_S = 3.0

# A pairing window is a loan. It closes on the first attempt that consumes it
# (success or failure) and, failing that, on this deadline — an open window
# lifts the bonded-proxy restriction and disarms the quarantine, so it must
# never outlive the user standing at the thermostat.
PAIRING_WINDOW_TTL_S = 180.0

# Poll interval imposed on a device that keeps failing to connect — either
# because pairing keeps timing out (the library's verdict) or simply because
# UNREACHABLE_THRESHOLD polls in a row failed for any reason at all. Never on a
# device that was explicitly rejected: that one is quarantined instead.
#
# The second, verdict-INDEPENDENT trigger is deliberate. The library's round
# counter is a fragile side channel — it needs a full round to complete, which
# needs the candidate arithmetic above to be right, which is exactly what broke
# twice. A device that has failed five polls in a row does not need a diagnosis
# to deserve a slower cadence, and hanging the only brake in the integration off
# a verdict that may never form is how a dead bond ended up polling forever.
#
# 15 minutes keeps a real recovery reachable without re-creating the prompt
# storm; the normal interval comes back on the first successful poll.
TIMEOUT_BACKOFF_INTERVAL_S = 900.0

# How recently a device must have completed an authenticated session for a
# "rejected" verdict to be treated as contradicted rather than proven.
#
# pymadoka classifies refusals from error TEXT ("insufficient authentication",
# "pairing failed", ATT error=5), and that text is genuinely ambiguous: the
# BRC1H accepts a single central, so a stale proxy link — or plain contention
# while every coordinator reconnects at once after an HA restart — produces it
# without the bond having gone anywhere. Its own _rejected_sources then retains
# the accusation until that exact path authenticates again.
#
# A bond does not evaporate minutes after it worked, so inside this window the
# refusal is downgraded to the timeout tier (slow cadence, soft repair) instead
# of quarantining the device. Field incident 2026-08-07 (issue #51): a
# thermostat that connected and polled at 22:11:44 was quarantined at 22:14:00
# and stayed down 11 hours, because the rejection branch was the one place the
# cost asymmetry stated everywhere else in this file was not applied.
#
# 10 minutes covers a post-restart storm settling down while staying far short
# of "the bond was fine this morning": past it, a refusal is proof again.
AUTH_CORROBORATION_WINDOW_S = 600.0

# Discovery adverts below this RSSI are almost certainly a neighbour's BRC1H
# bleeding through a wall: don't offer a discovery card for a device the user
# can't actually pair with. Manual setup (async_step_user) is the escape
# hatch — it never filters on signal strength.
RSSI_DISCOVERY_FLOOR = -90
# Ceiling on the config-flow validation connect. Every config flow — initial
# setup, discovery confirmation, MAC change, reauth — is a moment where a human
# is GUARANTEED to be standing at the thermostat, so all of them run the
# USER_INITIATED profile: PAIRING_WINDOW_TIMEOUT inside PAIRING_CONNECT_TIMEOUT.
# It used to be the opposite: the flow left pair_timeout at pymadoka's 8s
# default under a 30s ceiling, so the one guaranteed-attended moment had the
# smallest pairing budget of the whole integration and a user who walked to the
# thermostat could not confirm in time.
VALIDATE_TIMEOUT = PAIRING_CONNECT_TIMEOUT
# Hard ceiling on one full-feature poll (queries retry individually).
POLL_TIMEOUT = 45

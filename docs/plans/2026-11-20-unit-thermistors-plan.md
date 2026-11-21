# Unit status thermistors (#162) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose the six thermistor readings of the app's "Unit status"
screen as diagnostic temperature sensors, read at most every 300 s and only
when one of them is enabled.

**Architecture:** pymadoka-ng gets a `UnitThermistors` feature whose
`query()` plays start monitoring, indoor page, outdoor page, stop
monitoring. `Controller` does not hold it. The daikin_madoka coordinator
owns one when a thermistor entity is enabled, runs it after
`controller.update()` when due, and hides values older than 900 s.

**Tech Stack:** Python 3.11+, pymadoka-ng (pytest, pytest-asyncio),
Home Assistant custom integration (pytest-homeassistant-custom-component in
the `madoka-tests-img` Docker image).

Design: `docs/plans/2026-11-20-unit-thermistors-design.md`.

---

## Part A: pymadoka-ng (worktree `../pymadoka-wt-therm`, branch `feat/unit-thermistors`)

Setup:

```bash
cd /c/Users/dasim/repos/homeassistant/pymadoka
git fetch -q && git worktree add -b feat/unit-thermistors ../pymadoka-wt-therm origin/main
```

Test command (from the worktree):

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ../daikin_madoka/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/test_unit_thermistors.py
```

### Task A1: decoding of one page answer

**Files:**
- Create: `pymadoka/features/unit_thermistors.py`
- Test: `tests/test_unit_thermistors.py`

**Step 1: failing tests**

```python
"""Unit status thermistors (functions 0x4111 and 0x0111), daikin_madoka#162.

Frames come from the HCI capture attached to #162 (BRC1H, FBA100A2VEB
indoor, AZAS100M7V1B outdoor), 2026-11-19 22:11.
"""

import asyncio
from unittest.mock import AsyncMock, call, patch

import pytest

from pymadoka.features.unit_thermistors import (
    INDOOR_IDS,
    OUTDOOR_IDS,
    UnitThermistors,
    _PageStatus,
)

PENDING = None  # marker: argument answered with length 0xFF


def frame(cmd: int, *args: tuple[int, bytes | None]) -> bytes:
    """A rebuilt answer: size, cmd id (3 bytes), then id/len/value."""
    body = bytearray([0x00, cmd >> 8, cmd & 0xFF])
    for arg_id, value in args:
        if value is PENDING:
            body += bytes([arg_id, 0xFF])
        else:
            body += bytes([arg_id, len(value)]) + value
    return bytes([len(body) + 1]) + bytes(body)


def page(page_no: int, values: dict[int, bytes | None]) -> bytes:
    return frame(0x0111, (0x01, bytes([page_no])), (0x02, b"\x00"), *values.items())


INDOOR_PENDING = page(0, {i: PENDING for i in INDOOR_IDS})
INDOOR_VALUES = page(0, {
    0x40: b"OFF", 0x41: b"\x00\x00", 0x42: b"\x00", 0x43: b"\x00\x00",
    0x44: b"\x00", 0x45: b"\x00", 0x46: b"\x00", 0x47: b"\x00",
    0x48: b"\x00", 0x49: b"\x01", 0x4A: b"\x00\x0e", 0x4B: b"\x00\x0e",
    0x4C: b"\x00\x0d", 0x4D: b"\xff\xff", 0x4E: b"\xff\xff", 0x4F: b"\xff\xff",
})
OUTDOOR_PENDING = page(1, {i: PENDING for i in OUTDOOR_IDS})
OUTDOOR_VALUES = page(1, {
    0x40: b"\x00\x00", 0x41: b"\x00\x00\x00", 0x43: b"\x00\x00", 0x44: b"\x00",
    0x45: b"\x01\x05", 0x46: b"\x01\x0d", 0x4A: b"\x00\x0a", 0x4B: b"\x00\x0d",
    0x4C: b"\x00\x25",
})
MONITOR_ACK = frame(0x4111, (0xFE, b"\x00"))


def parsed(data: bytes) -> _PageStatus:
    status = _PageStatus(0, INDOOR_IDS)
    status.parse(bytearray(data))
    return status


def test_the_indoor_request_is_the_app_frame():
    assert bytes(_PageStatus(0, INDOOR_IDS).serialize()) == bytes(
        [0x01, 0x01, 0x00, 0x02, 0x01, 0x00]
        + [b for i in range(0x40, 0x50) for b in (i, 0x00)]
    )


def test_the_outdoor_request_asks_only_the_arguments_the_page_has():
    assert bytes(_PageStatus(1, OUTDOOR_IDS).serialize()) == bytes(
        [0x01, 0x01, 0x01, 0x02, 0x01, 0x00]
        + [b for i in (0x40, 0x41, 0x43, 0x44, 0x45, 0x46, 0x4A, 0x4B, 0x4C) for b in (i, 0x00)]
    )


def test_an_answer_of_0xff_lengths_is_pending():
    assert parsed(INDOOR_PENDING).pending is True


def test_values_are_signed_big_endian_degrees():
    status = parsed(INDOOR_VALUES)
    assert status.pending is False
    assert status.temperature(0x4A) == 14
    assert status.temperature(0x4C) == 13
    assert parsed(page(0, {0x4A: b"\xff\xfb"})).temperature(0x4A) == -5


def test_ffff_means_not_fitted():
    assert parsed(INDOOR_VALUES).temperature(0x4D) is None


def test_an_empty_page_is_not_pending_and_has_no_values():
    status = parsed(page(1, {i: b"" for i in OUTDOOR_IDS}))
    assert status.pending is False
    assert status.temperature(0x4A) is None
```

**Step 2:** run, expect `ModuleNotFoundError: pymadoka.features.unit_thermistors`.

**Step 3: implementation (decoding part)**

```python
"""This module reads the thermistor temperatures the Madoka Assistant app
shows on its "Unit status" screen.

Sequence found by the reporter of daikin_madoka#162 from an HCI capture of
the app, and replayed from an ESP32:

1. Function 0x4111, argument 0xFE = 1: start thermistor monitoring.
2. Function 0x0111, page 0 (indoor): arguments 0x01 = page, 0x02 = unit 0,
   then 0x40 to 0x4F with length 0.
3. Function 0x0111, page 1 (outdoor): same, but only the arguments that
   page has (OUTDOOR_IDS); asking for others empties the answer.
4. Function 0x4111, argument 0xFE = 0: stop monitoring.

The first answers to a page carry length 0xFF for every argument while the
unit acquires the readings; the app asks again every 0.5 to 0.7 s and the
values arrive on the third or fourth answer. The outdoor page answers empty
while the indoor one is still pending, so it is only asked afterwards.

Arguments 0x4A, 0x4B, 0x4C are 2-byte big-endian signed whole degrees
Celsius, FF FF when the sensor is not fitted:

    0x4A  indoor Th1 suction air       outdoor Th1 outdoor air
    0x4B  indoor Th2 liquid pipe       outdoor Th2 heat exchanger
    0x4C  indoor Th3 gas pipe          outdoor Th3 compressor discharge

The other arguments (indoor 0x40 = ASCII "OFF", 0x49, outdoor 0x45/0x46)
are not understood and not exposed.
"""

import asyncio
import logging
from typing import Dict, Optional, Sequence

from pymadoka.connection import Connection
from pymadoka.feature import Feature, FeatureStatus, NotImplementedException, ParseException

logger = logging.getLogger(__name__)

MONITOR_CMD_ID = 0x4111
PAGE_CMD_ID = 0x0111
INDOOR_PAGE = 0x00
OUTDOOR_PAGE = 0x01
INDOOR_IDS: Sequence[int] = tuple(range(0x40, 0x50))
OUTDOOR_IDS: Sequence[int] = (0x40, 0x41, 0x43, 0x44, 0x45, 0x46, 0x4A, 0x4B, 0x4C)
TH1, TH2, TH3 = 0x4A, 0x4B, 0x4C
PENDING_SIZE = 0xFF


class _PageStatus(FeatureStatus):
    """One answer of function 0x0111 for one page.

    The base parser turns a 0xFF length into an empty value, which would make
    "still acquiring" look like "page has no such sensor"; this one keeps the
    two apart.
    """

    def __init__(self, page: int, ids: Sequence[int]):
        self.page = page
        self.ids = ids
        self.values: Dict[int, bytes] = {}
        self.pending = False

    def get_values(self) -> Dict[int, bytearray]:
        out: Dict[int, bytearray] = {0x01: bytes([self.page]), 0x02: bytes([0x00])}
        for arg_id in self.ids:
            out[arg_id] = b""
        return out

    def set_values(self, values: Dict[int, bytearray]):
        self.values = {k: bytes(v) for k, v in values.items()}

    def parse(self, data: bytearray):
        if len(data) < 4 or data[0] != len(data):
            raise ParseException("Message size and data size mismatch")
        values: Dict[int, bytes] = {}
        pending = False
        i = 4
        while i < len(data):
            if i + 1 >= len(data):
                raise ParseException("Truncated argument header")
            arg_id, size = data[i], data[i + 1]
            if size == PENDING_SIZE:
                pending |= arg_id in (TH1, TH2, TH3)
                size = 0
            if i + 2 + size > len(data):
                raise ParseException("Truncated argument value")
            values[arg_id] = bytes(data[i + 2 : i + 2 + size])
            i += 2 + size
        self.pending = pending
        self.set_values(values)

    def temperature(self, arg_id: int) -> Optional[int]:
        value = self.values.get(arg_id)
        if value is None or len(value) != 2 or value == b"\xff\xff":
            return None
        return int.from_bytes(value, "big", signed=True)
```

**Step 4:** run, expect 6 passed.

**Step 5:** commit `feat: decode the unit status thermistor pages (#162 of daikin_madoka)`.

### Task A2: the read sequence

**Files:** same two.

**Step 1: failing tests** (append)

```python
def feature(*answers: bytes) -> tuple[UnitThermistors, AsyncMock]:
    thermistors = UnitThermistors(connection=None)
    send = AsyncMock(side_effect=list(answers))
    thermistors._send_command = send
    return thermistors, send


START = call(0x4111, bytearray([0xFE, 0x01, 0x01]))
STOP = call(0x4111, bytearray([0xFE, 0x01, 0x00]))


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("pymadoka.features.unit_thermistors.asyncio.sleep", AsyncMock()) as sleep:
        yield sleep


async def test_a_read_plays_the_app_sequence():
    thermistors, send = feature(
        MONITOR_ACK, INDOOR_PENDING, INDOOR_PENDING, INDOOR_VALUES,
        OUTDOOR_PENDING, OUTDOOR_VALUES, MONITOR_ACK,
    )

    status = await thermistors.query()

    assert send.await_args_list[0] == START
    assert send.await_args_list[-1] == STOP
    assert [c.args[1][2] for c in send.await_args_list[1:-1]] == [0, 0, 0, 1, 1]
    assert (status.indoor_suction, status.indoor_liquid_pipe, status.indoor_gas_pipe) == (14, 14, 13)
    assert (status.outdoor_air, status.outdoor_heat_exchanger, status.outdoor_discharge) == (10, 13, 37)
    assert thermistors.status is status


async def test_an_empty_outdoor_page_leaves_the_outdoor_values_empty():
    empty = page(1, {i: b"" for i in OUTDOOR_IDS})
    thermistors, _ = feature(MONITOR_ACK, INDOOR_VALUES, empty, MONITOR_ACK)

    status = await thermistors.query()

    assert status.indoor_suction == 14
    assert status.outdoor_air is None


async def test_a_page_that_never_fills_gives_up_after_six_answers_and_stops():
    thermistors, send = feature(MONITOR_ACK, *[INDOOR_PENDING] * 6, MONITOR_ACK)

    with pytest.raises(asyncio.TimeoutError):
        await thermistors.query()

    assert send.await_count == 8
    assert send.await_args_list[-1] == STOP
    assert thermistors.status is None


async def test_stop_is_sent_after_an_error_and_its_own_failure_is_ignored():
    thermistors, send = feature(MONITOR_ACK, ConnectionError("gone"), ConnectionError("gone"))

    with pytest.raises(ConnectionError):
        await thermistors.query()

    assert send.await_args_list[-1] == STOP


def test_it_cannot_be_written():
    from pymadoka.feature import NotImplementedException
    with pytest.raises(NotImplementedException):
        UnitThermistors(connection=None).update_cmd_id()
```

Note: pytest-asyncio mode. Check `pyproject.toml` `[tool.pytest.ini_options]`
for `asyncio_mode = "auto"`; if absent, mark the async tests with
`@pytest.mark.asyncio` as `tests/test_transport.py` does.

**Step 2:** run, expect ImportError on `UnitThermistors`.

**Step 3: implementation** (append to the module)

```python
MAX_PAGE_ANSWERS = 6
PAGE_RETRY_DELAY = 0.6


class _MonitorStatus(FeatureStatus):
    """Function 0x4111: argument 0xFE starts (1) or stops (0) monitoring."""

    def __init__(self, on: bool):
        self.on = on

    def get_values(self) -> Dict[int, bytearray]:
        return {0xFE: bytes([0x01 if self.on else 0x00])}

    def set_values(self, values: Dict[int, bytearray]):
        pass


class UnitThermistorsStatus(FeatureStatus):
    """Thermistor temperatures in whole degrees Celsius; None if not fitted
    or not reported.
    """

    def __init__(self):
        self.indoor_suction: Optional[int] = None
        self.indoor_liquid_pipe: Optional[int] = None
        self.indoor_gas_pipe: Optional[int] = None
        self.outdoor_air: Optional[int] = None
        self.outdoor_heat_exchanger: Optional[int] = None
        self.outdoor_discharge: Optional[int] = None

    def set_pages(self, indoor: _PageStatus, outdoor: _PageStatus):
        self.indoor_suction = indoor.temperature(TH1)
        self.indoor_liquid_pipe = indoor.temperature(TH2)
        self.indoor_gas_pipe = indoor.temperature(TH3)
        self.outdoor_air = outdoor.temperature(TH1)
        self.outdoor_heat_exchanger = outdoor.temperature(TH2)
        self.outdoor_discharge = outdoor.temperature(TH3)

    def get_values(self) -> Dict[int, bytearray]:
        return {}

    def set_values(self, values: Dict[int, bytearray]):
        raise NotImplementedException("Filled by UnitThermistors.query from two pages")


class UnitThermistors(Feature):
    """Reads the unit status thermistors. Not held by Controller: one read is
    about ten round trips, so the caller decides how often to run it.
    """

    def __init__(self, connection: Connection):
        self.status = None
        super().__init__(connection)

    def query_cmd_id(self) -> int:
        return PAGE_CMD_ID

    def update_cmd_id(self) -> int:
        raise NotImplementedException("This feature cannot be updated")

    def new_status(self) -> FeatureStatus:
        return UnitThermistorsStatus()

    async def _read_page(self, page: int, ids: Sequence[int]) -> _PageStatus:
        for attempt in range(MAX_PAGE_ANSWERS):
            if attempt:
                await asyncio.sleep(PAGE_RETRY_DELAY)
            answer = _PageStatus(page, ids)
            answer.parse(bytearray(await self._send_command(PAGE_CMD_ID, answer.serialize())))
            if not answer.pending:
                return answer
        raise asyncio.TimeoutError(
            f"Thermistor page {page} still pending after {MAX_PAGE_ANSWERS} answers"
        )

    async def query(self) -> FeatureStatus:
        """Start monitoring, read the indoor then the outdoor page, stop."""
        await self._send_command(MONITOR_CMD_ID, _MonitorStatus(True).serialize())
        try:
            indoor = await self._read_page(INDOOR_PAGE, INDOOR_IDS)
            outdoor = await self._read_page(OUTDOOR_PAGE, OUTDOOR_IDS)
        finally:
            try:
                await self._send_command(MONITOR_CMD_ID, _MonitorStatus(False).serialize())
            except Exception as err:  # the unit stops on its own; see module doc
                logger.debug(f"Could not stop thermistor monitoring: {err}")
        status = UnitThermistorsStatus()
        status.set_pages(indoor, outdoor)
        self.status = status
        return status
```

**Step 4:** run the file, expect all passed; then the whole suite, expect no
regression.

**Step 5:** commit `feat: read the unit status thermistors`.

### Task A3: release prep

**Files:** `pyproject.toml` (version `0.6.0`), `CHANGELOG.md` (CRLF: use the
Edit tool), `README.md` feature list if it lists features.

CHANGELOG entry heading `## v0.6.0`, body: what `UnitThermistors` reads,
that `Controller` does not hold it and why, credit to the #162 reporter for
the capture, not yet validated on the maintainer's units.

Commit `chore: release 0.6.0`, push, `gh pr create --repo dasimon135/pymadoka`,
wait for CI green, merge. **Stop: David uploads 0.6.0 to PyPI.**

---

## Part B: daikin_madoka (worktree `../daikin_madoka-wt-therm`, branch `feat/unit-thermistors`)

Test command (Docker, wheel of the Part A worktree built into `dist/` until
PyPI has 0.6.0):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "C:/Users/dasim/repos/homeassistant/daikin_madoka-wt-therm:/src" -v "C:/Users/dasim/repos/homeassistant/pymadoka-wt-therm/dist:/wheels" -w /src madoka-tests-img:latest bash -c "pip install -q /wheels/*.whl; grep -v pymadoka requirements-test.txt > /tmp/r.txt; pip install -q -r /tmp/r.txt; python -m pytest -q -p no:cacheprovider tests"
```

### Task B1: constants

**File:** `custom_components/daikin_madoka/const.py`, next to `ENERGY_SCAN_INTERVAL`:

```python
# Unit status thermistors (#162): one read is about ten round trips, so it
# runs at most every THERMISTOR_SCAN_INTERVAL seconds, gives up after
# THERMISTOR_READ_TIMEOUT, and a value older than THERMISTOR_MAX_AGE (three
# missed reads) is no longer shown.
THERMISTOR_SCAN_INTERVAL = 300
THERMISTOR_READ_TIMEOUT = 20
THERMISTOR_MAX_AGE = 900
# unique_id suffix -> UnitThermistorsStatus attribute
THERMISTOR_SENSORS = {
    "thermistor_indoor_suction": "indoor_suction",
    "thermistor_indoor_liquid_pipe": "indoor_liquid_pipe",
    "thermistor_indoor_gas_pipe": "indoor_gas_pipe",
    "thermistor_outdoor_air": "outdoor_air",
    "thermistor_outdoor_heat_exchanger": "outdoor_heat_exchanger",
    "thermistor_outdoor_discharge": "outdoor_discharge",
}
```

### Task B2: coordinator owns, schedules and ages the read

**Files:**
- Modify: `custom_components/daikin_madoka/coordinator.py`
- Test: `tests/test_thermistors.py` (new)

**Step 1: failing tests**

```python
"""Unit status thermistors: read only when enabled, at most every 300 s (#162)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException

from custom_components.daikin_madoka.const import (
    THERMISTOR_MAX_AGE,
    THERMISTOR_SCAN_INTERVAL,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MONO = "custom_components.daikin_madoka.coordinator.monotonic"


def _coordinator(query: AsyncMock | None) -> MadokaCoordinator:
    coordinator = object.__new__(MadokaCoordinator)
    coordinator.controller = MagicMock()
    coordinator.controller.connection.address = "D0:CF:13:0F:11:F6"
    if query is not None:
        coordinator.thermistors = MagicMock(query=query, status=SimpleNamespace(outdoor_air=10))
    return coordinator


async def test_nothing_is_read_without_an_enabled_entity() -> None:
    coordinator = _coordinator(None)
    await coordinator._async_read_thermistors()
    assert coordinator.thermistor_status is None


async def test_a_read_runs_at_most_every_scan_interval() -> None:
    query = AsyncMock()
    coordinator = _coordinator(query)
    with patch(MONO, return_value=1000.0):
        await coordinator._async_read_thermistors()
    with patch(MONO, return_value=1000.0 + THERMISTOR_SCAN_INTERVAL - 1):
        await coordinator._async_read_thermistors()
    assert query.await_count == 1
    with patch(MONO, return_value=1000.0 + THERMISTOR_SCAN_INTERVAL):
        await coordinator._async_read_thermistors()
    assert query.await_count == 2


async def test_a_failed_read_is_retried_next_poll_and_does_not_raise() -> None:
    query = AsyncMock(side_effect=[TimeoutError(), ConnectionException("x"), None])
    coordinator = _coordinator(query)
    with patch(MONO, return_value=1000.0):
        await coordinator._async_read_thermistors()
        await coordinator._async_read_thermistors()
        await coordinator._async_read_thermistors()
    assert query.await_count == 3
    assert coordinator.thermistor_status is not None


async def test_values_expire_after_max_age() -> None:
    coordinator = _coordinator(AsyncMock())
    with patch(MONO, return_value=1000.0):
        await coordinator._async_read_thermistors()
    with patch(MONO, return_value=1000.0 + THERMISTOR_MAX_AGE):
        assert coordinator.thermistor_status.outdoor_air == 10
    with patch(MONO, return_value=1000.0 + THERMISTOR_MAX_AGE + 1):
        assert coordinator.thermistor_status is None
```

Plus, in the same file, the gating test with the real `hass` fixture: register
`sensor.x` with unique_id `f"{MAC}_thermistor_outdoor_air"` in the entity
registry (`er.async_get(hass).async_get_or_create("sensor", DOMAIN, ...)`),
build the coordinator as `tests/test_platforms.py::_coordinator` does, assert
`coordinator.thermistors is not None`; same with `disabled_by=
er.RegistryEntryDisabler.INTEGRATION`, assert `None`.

**Step 2:** run, expect AttributeError on `_async_read_thermistors`.

**Step 3: implementation**

Imports: `from homeassistant.helpers import entity_registry as er`,
`from pymadoka.features.unit_thermistors import UnitThermistors, UnitThermistorsStatus`,
the three constants and `THERMISTOR_SENSORS`.

Class attributes on `MadokaCoordinator` (so `object.__new__` test doubles
and VAM coordinators have them too):

```python
    # Unit status thermistors (#162); None unless one of their entities is
    # enabled. Home Assistant reloads the entry when one gets enabled.
    thermistors: UnitThermistors | None = None
    _thermistors_read_at: float | None = None
```

In `__init__`, after `self.async_apply_energy_enabled(energy_enabled)`:

```python
        if _thermistor_entity_enabled(hass, controller.connection.address):
            self.thermistors = UnitThermistors(controller.connection)
```

Module-level helper:

```python
def _thermistor_entity_enabled(hass: HomeAssistant, address: str) -> bool:
    """Whether any thermistor sensor of this device is enabled in the registry."""
    registry = er.async_get(hass)
    for key in THERMISTOR_SENSORS:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{address}_{key}")
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is not None and entry.disabled_by is None:
            return True
    return False
```

Check first that `coordinator.address` and the unique_id prefix use the
same normalisation as `controller.connection.address` (`def address` near
the end of the class); use whichever the entities use.

Methods:

```python
    async def _async_read_thermistors(self) -> None:
        """Read the thermistors when due; a failure never fails the poll."""
        if self.thermistors is None:
            return
        last = self._thermistors_read_at
        if last is not None and monotonic() - last < THERMISTOR_SCAN_INTERVAL:
            return
        try:
            async with asyncio.timeout(THERMISTOR_READ_TIMEOUT):
                await self.thermistors.query()
        except (TimeoutError, ConnectionAbortedError, ConnectionException) as err:
            _LOGGER.debug("Thermistor read of %s failed: %s", self.address, _describe(err))
            return
        self._thermistors_read_at = monotonic()

    @property
    def thermistor_status(self) -> UnitThermistorsStatus | None:
        """The last thermistor read, or None once older than THERMISTOR_MAX_AGE."""
        last = self._thermistors_read_at
        if self.thermistors is None or last is None:
            return None
        if monotonic() - last > THERMISTOR_MAX_AGE:
            return None
        return self.thermistors.status
```

In `_async_poll`, after the `if not status: raise UpdateFailed(...)` block
and before `_async_backfill_device_info()`:

```python
        await self._async_read_thermistors()
```

The test doubles call `self.address`; if that property reads attributes the
`object.__new__` double lacks, set them in `_coordinator()` of the test.

**Step 4:** run `tests/test_thermistors.py`, then the full suite.

**Step 5:** commit `feat: read the unit status thermistors when a sensor is enabled`.

### Task B3: six sensors

**Files:**
- Modify: `custom_components/daikin_madoka/sensor.py`
- Modify: `strings.json`, `translations/en.json`, `fr.json`, `es.json`, `it.json`
- Test: `tests/test_platforms.py`

**Step 1: failing tests** in `test_platforms.py`

- `test_sensor_setup_creates_all_sensors`: the six
  `f"{MAC}_thermistor_*"` unique_ids are present.
- `test_ventilation_setup_skips_the_outdoor_sensor`: none of them for a VAM.
- new `test_thermistor_sensors_are_disabled_diagnostics_reading_the_coordinator`:
  each has `entity_registry_enabled_default is False`,
  `entity_category is EntityCategory.DIAGNOSTIC`; with
  `coordinator.thermistors = MagicMock(status=SimpleNamespace(outdoor_air=10, ...))`
  and `coordinator._thermistors_read_at = monotonic()`, the outdoor air one
  reads 10; with `_thermistors_read_at = None` it reads None.

**Step 2:** run, expect failures.

**Step 3: implementation** in `sensor.py`

```python
class MadokaThermistorSensor(MadokaTemperatureSensor):
    """A unit status thermistor (#162), read every 5 min while enabled."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MadokaCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._attribute = THERMISTOR_SENSORS[key]

    @property
    def native_value(self) -> int | None:
        """Return the reading, None when not fitted or too old."""
        status = self.coordinator.thermistor_status
        if status is None:
            return None
        value: int | None = getattr(status, self._attribute)
        return value
```

In `async_setup_entry`, inside `if not is_ventilation:`:

```python
            entities.extend(
                MadokaThermistorSensor(coordinator, key) for key in THERMISTOR_SENSORS
            )
```

Translations, under `entity.sensor`:

| key | en | fr | es | it |
|---|---|---|---|---|
| thermistor_indoor_suction | Suction air temperature | Température air aspiré | Temperatura aire de aspiración | Temperatura aria aspirata |
| thermistor_indoor_liquid_pipe | Liquid pipe temperature | Température tuyau liquide | Temperatura tubería de líquido | Temperatura tubo liquido |
| thermistor_indoor_gas_pipe | Gas pipe temperature | Température tuyau gaz | Temperatura tubería de gas | Temperatura tubo gas |
| thermistor_outdoor_air | Outdoor unit air temperature | Température air groupe extérieur | Temperatura aire unidad exterior | Temperatura aria unità esterna |
| thermistor_outdoor_heat_exchanger | Outdoor heat exchanger temperature | Température échangeur extérieur | Temperatura intercambiador exterior | Temperatura scambiatore esterno |
| thermistor_outdoor_discharge | Compressor discharge temperature | Température refoulement compresseur | Temperatura descarga compresor | Temperatura mandata compressore |

**Step 4:** run the full suite (it includes `test_translations.py`).

**Step 5:** commit `feat: unit status thermistor sensors`.

### Task B4: real hardware, then release

1. Pin `pymadoka-ng==0.6.0` in `manifest.json` and `requirements-test.txt`
   once David has uploaded it; bump `manifest.json` to `3.16.0b2`;
   CHANGELOG `## v3.16.0-beta.2 - November 2026`.
2. Push, `gh pr create --repo dasimon135/daikin_madoka`, CI green (rerun
   with `gh run rerun --failed` if PyPI has not propagated yet), merge.
3. `deploy.ps1` to H:, `ha_restart`, enable "Outdoor heat exchanger
   temperature" on one BRC1H, wait for the reload, turn on debug for
   `pymadoka.features.unit_thermistors`, and read one sequence in the log.
   Compare with the app on that unit if David can open it.
4. If the unit answers differently (one page only, other ids), fix in
   pymadoka 0.6.1 before any release.
5. `scripts/release_notes.py`, hand David `gh release create v3.16.0-beta.2
   ... --prerelease --target <full SHA>`, draft the #162 reply into
   `ha-support-automation/work/`.

# Changelog

## v3.10.0 - August 2026

Thermostat screens stop being asked to confirm pairings nobody can answer.

### The pairing prompts on the thermostat screen are gone

The integration has restricted automatic reconnects to proxies known to hold a bond since v3.6.0, by filtering the candidate list it hands to pymadoka. That restriction was advisory and could not have been anything else: Home Assistant keeps only the *address* of the `BLEDevice` it is given and re-scores every connectable path itself at connect time. So a reconnect could — and regularly did — land on a proxy the integration had deliberately excluded, and pairing there put a six-digit confirmation prompt on the thermostat screen that no unattended retry can ever answer.

Field measurement behind this release: one thermostat produced eight such prompts in a single hour, every one of them through a proxy absent from its `bonded_sources`. Nothing was visible in Home Assistant — the device stayed available throughout, because the next candidate succeeded — so the only trace was a log line and an illuminated screen in the living room.

The check now happens where it binds: after the link is up and *before* `pair()`, against the path the backend really used. An unsanctioned path is dropped without pairing. It costs a connect and a disconnect, no SMP exchange, and nothing appears on the thermostat.

Worth stating plainly: the scoring that picks these paths is not simply "nearest proxy wins". In the measured case the elected proxy was 15 dB *weaker* than an available bonded one, and won because free-slot and failure-count penalties outweighed the signal difference. Any of the proxies in range can be elected at any time, which is why the veto had to move to the real path rather than the offered one.

### A new repair for the case only a human can fix

If every path Home Assistant chooses stays unsanctioned for three consecutive rounds, a new `unbonded_path` repair names the proxy the connection keeps landing on and offers the reauth flow. This replaces harassing the thermostat with telling the user, in Home Assistant, which proxy to go and pair with.

It is a WARNING, not an error, and it convicts nobody: no bond is evicted and reconnects are not suspended, because nothing was refused — no pairing was attempted at all. The poll cadence is slowed to 15 minutes because the retries are futile while the scoring stands, not because the thermostat did anything wrong. The condition often clears on its own when signal conditions shift.

### What it costs when the veto is the only thing left

Stated plainly, because a day of field observation made it concrete rather than
theoretical. When Home Assistant elects an unsanctioned proxy for every attempt
of a round — which it will, since it re-scores each attempt and a free proxy
scores well — the veto refuses all of them and the round cannot fall through to
a bonded path. The device then goes UNAVAILABLE.

That is the intended trade, and it is worth being deliberate about: a thermostat
whose entities are unavailable, with a repair naming the proxy to pair with, is
better than one that works while lighting a six-digit code nobody answers, every
night, invisibly. But it is a real cost and it is not hypothetical.

Measured 2026-08-29 on the maintainer's install. The stale proxy was dropped at
15:12; the veto then refused it on every round and the thermostat was
unreachable from 16:29. At 16:52 the scoring shifted — the good proxy's failure
count had decayed, putting it back in front by a single point — the round fell
through to it, and the device recovered on its own with no pairing, no prompt
and no user action. Both repairs closed themselves.

So the unavailability lasts as long as the scoring insists, which is minutes to
hours, and the Reconnect button ends it deliberately at any time.

### bonded_sources no longer claims a key the proxy has lost

The bonded-proxy list records what a session once succeeded through. It is not a reading of the proxy's keystore, and the two drift apart — which matters more than it sounds, because the new veto trusts that list: a stale entry is precisely a path it will still allow pairing on.

Measured on the maintainer's install the same evening: one proxy listed as bonded for two thermostats had lost both their keys while keeping a third's. Every attempt through it therefore started a real numeric-comparison pairing. This is not inferred — the ESPHome pairing responders pushed the passkeys into Home Assistant as notifications (two of them, minutes apart), and a second integration independently reported "this proxy does not have the pairing key" for the same two devices. Each of those attempts left a six-digit code lit on a thermostat, waiting for a confirmation nobody was there to give, until it timed out.

A proxy is now dropped from the list after five consecutive pairing timeouts unbroken by any success on that same proxy. The evidence is deliberately not "it timed out" — congestion produces that too — but "it never succeeds": a valid bond re-encrypts silently as soon as the proxy is free, and a single success clears the streak, while a keyless path cannot complete on its own at all because completing it requires a person at the thermostat.

Both eviction triggers — proven refusals and stale-key timeouts — now share one routine, so the safety rules cannot hold for one and not the other. Chief among them: the last known bond is never dropped, because an empty list reads as "unrestricted" everywhere and emptying it would switch the entire anti-prompt policy off instead of tightening it.

Being wrong is cheap and self-announcing: a wrongly dropped path stops being paired on, and if it was the only usable one, the unbonded_path repair names it and a single Reconnect restores it.

### A proxy that proved it holds no key is no longer forgiven every round

Found on live hardware while this release was under observation, and it is the
half that made the eviction above unreachable in practice.

A proxy refused a thermostat's bond outright — the strongest evidence there
is — and then timed out on every round afterwards, because that is what a
keyless proxy does: the prompt goes up on the screen and nobody answers it.
The library retains a proven refusal precisely so the later rounds inherit it,
but the retention was consulted one branch too late to ever see a timeout, so
the verdict decayed to "timeout" from the second round onward and the
integration, which acts only on proven refusals, charged nobody. The proxy
kept its place in `bonded_sources` — the very list the veto trusts — and every
reconnect lit a fresh six-digit code with nothing able to conclude otherwise.

The arithmetic left over was hopeless: reaching eviction through the timeout
route alone takes 3 library rounds x 3 candidates x 5 errors, about 45 pairing
attempts, every one of them consecutive and every one of them a prompt.

Fixed in pymadoka-ng 0.3.13. Nothing in this integration changed: it was
already asking the right question, and now gets a truthful answer.

### Congestion does not pick a favourite proxy

The eviction above waits for five timeout rounds on one path because a timeout
is ambiguous: congestion produces one on a perfectly good bond, and convicting
a healthy proxy costs a re-pair with a human standing at the thermostat. Held
against the field, that bar was never cleared — one proxy took every attempt of
every round for seventeen hours while the others polled the same thermostat
normally in between, and every round lit a fresh six-digit code.

But congestion is a property of the air, not of a proxy. It cannot make one
path time out while another authenticates against the same thermostat minutes
apart. So a successful session on a DIFFERENT path, while this one's streak is
running, removes the only innocent explanation the streak had, and two rounds
then suffice instead of five.

This is the mirror of the rule that already governs refusals, where a session
completed minutes earlier downgrades a "refused the bond" verdict to the
timeout tier. The same evidence, read in the other direction.

Nothing is convicted on congestion alone: with no contemporaneous success
elsewhere the long streak still applies, and any success on the accused path
itself clears both the streak and the corroboration.

### Requires pymadoka-ng 0.3.13

The veto lives in the library (`allowed_sources_callback`), which the integration now supplies from the same bonded-proxy list that orders the candidates — one definition, so the two can never disagree about what is allowed.

### The card no longer renders as "custom element doesn't exist"

Reported as intermittent with no pattern anyone could pin down ([#65](https://github.com/dasimon135/daikin_madoka/issues/65)).

The dashboard resource points at `/daikin_madoka/madoka-card.js`, which the frontend requests on every page load. The static path behind that URL was registered from `async_setup_entry` — which only runs once the Bluetooth stack is up and the thermostats have had their chance to answer, seconds after Home Assistant starts accepting HTTP requests. In that window the URL returned 404, the browser parsed the error page as a JavaScript module, the custom element was never defined, and the card stayed broken on that page until it was reloaded by hand.

Opening a dashboard right after restarting Home Assistant is exactly how one lands in that window, which is what made the failure look random.

Serving the card is a component-level concern, not an entry-level one, so it moves to `async_setup`: it runs when the component is imported, before any entry, and regardless of whether any entry succeeds. ([#81](https://github.com/dasimon135/daikin_madoka/pull/81))

### The device page carries a model and a revision again

Setup called `read_info()` immediately after building the controller, before the BLE link existed. pymadoka returns an empty dict in that state without raising and without caching it, and nothing ever retried, so the model marking and firmware revision never reached the device registry: every device page showed a bare BRC1H with no version, on every installation. It is now read from a poll that has just succeeded, and stops after `DEVICE_INFO_MAX_ATTEMPTS` so a controller that publishes no Device Information service is not re-enumerated on every cycle.

Reading it on real hardware settled what that service actually describes: `UE878 RF MODULE` by Universal Electronics, Model Number String `0.1`, Software Revision `7031.05.17` — the BRC1H's **Bluetooth radio**, not the Daikin controller. So `model=f"BRC1H{model}"` was inventing a marking (it rendered as `BRC1H0.1` on all four units here), and the revisions belong to the radio. They are reported as such; the Daikin firmware version is not exposed over GATT at all. ([#78](https://github.com/dasimon135/daikin_madoka/issues/78), [#79](https://github.com/dasimon135/daikin_madoka/pull/79))

## v3.9.2 - August 2026

A follow-up to v3.9.1. The setpoint fix released there was right for the units it was reported from and wrong for a unit that keeps a minimum gap between its two setpoints.

### The temperature no longer lands a degree off on units that enforce a setpoint gap

v3.9.1 wrote the same value to both setpoint registers whenever the unit was not range-capable. That is what a `min_differential = 0` unit wants — it stores the pair as sent. A unit reporting a non-zero minimum differential cannot hold an equal pair at all: the frame applies cooling first, heating then breaks the gap, and the firmware restores it by pushing the cooling register up. Every change read back a degree high, and lowering the target by one degree looked like a dead button.

The written pair now carries the unit's own `min_differential` (argument `0x32`): the setpoint the active mode uses gets the target, and the other one is placed a differential away from it, so the controller has nothing to correct. On a unit reporting `0` this is byte for byte what v3.9.1 sent, so the units [#67](https://github.com/dasimon135/daikin_madoka/issues/67) was reported from are unaffected. A controller that does not report the argument keeps the v3.9.1 behaviour.

Confirmed on the affected hardware by @speynaud, in every mode: the target lands where it was asked for and it survives the poll, raising and lowering alike. The release shipped with COOL confirmed and HEAT derived from the same rule; HEAT was measured the day after and behaves as derived.

The ESPHome component now reads and applies the same differential. ([#65](https://github.com/dasimon135/daikin_madoka/issues/65), [#67](https://github.com/dasimon135/daikin_madoka/issues/67))

### Upgrading

Update via HACS and restart. Nothing to reconfigure; entity IDs and history are preserved. The bundled library stays at **pymadoka-ng 0.3.11**. ESPHome component users need to rebuild to pick up the setpoint fix.

## v3.9.1 - August 2026

Two field reports, two fixes. Both came from users running configurations I do not have here.

### Changing the temperature did nothing on a single-setpoint unit

On a BRC1H configured for single-setpoint logic (*Setpoint logic → Single setpoint*, so `range_enabled = 0`), setting a target temperature from Home Assistant was a no-op: reads were correct, ON/OFF worked, the thermostat accepted the same change from its own screen — only the write from HA was dropped, and dropped in silence. The unit answers with its usual UPDATE frame and the immediate re-read returns the old value.

The setpoint frame carries both registers, cooling and heating, and only the one belonging to the active mode was filled. In COOL that sent cooling = 22 alongside an unchanged heating = 24. A unit with `range_enabled = 0` never holds a mismatched pair and rejects a frame that asks it to — the same silent failure mode as the zeroed limits fixed in v2.4.0, one field over. AUTO was unaffected because both branches fired there, which is why this only ever showed in COOL and HEAT.

Both setpoints are now written to the target whenever the unit is not range-capable. On a range-capable unit outside AUTO the previous behaviour is kept, so the setpoint that unit still uses in AUTO survives. The ESPHome component had the same bug in its single-setpoint branch and is fixed with it. ([#67](https://github.com/dasimon135/daikin_madoka/issues/67))

### Setup no longer fails because of another integration's devices

The orphan-device housekeeping pass assumed every device identifier in the Home Assistant registry held exactly two elements. They can hold more — `rfxtrx` uses four — and unpacking two names out of one raised `ValueError: too many values to unpack`. Since that pass runs on every entry setup, a single such device anywhere in the registry stopped this integration from starting at all. The domain is now read by index. ([#63](https://github.com/dasimon135/daikin_madoka/issues/63))

### Upgrading

Update via HACS and restart. Nothing to reconfigure; entity IDs and history are preserved. The bundled library stays at **pymadoka-ng 0.3.11**. ESPHome component users need to rebuild to pick up the setpoint fix.

## v3.9.0 - August 2026

Requires **pymadoka-ng 0.3.11** (installed automatically).

**Two ways a perfectly healthy thermostat could be declared unpaired and locked out until somebody walked to it.** Both are fixed, and both were confirmed on real hardware rather than reasoned about.

### A thermostat that just worked is no longer quarantined as unpaired

After a Home Assistant restart, a thermostat could connect, poll successfully, and then be declared "pairing required" a couple of minutes later — automatic reconnects stopped and it stayed down until somebody walked to the unit and re-paired it (11 hours, in the case that prompted this).

Refusals are classified from the Bluetooth error text, and the BRC1H accepts only one central at a time, so a stale proxy link — or simply every thermostat reconnecting at once after a restart — produces exactly the same "insufficient authentication" as a bond that is genuinely gone. A refusal arriving within 10 minutes of a completed, authenticated session is now treated as congestion: reconnects slow to one every 15 minutes and a warning appears, but nothing is quarantined and nobody is summoned to the thermostat. One good session excuses one refusal — a bond that really is dead still surfaces on the next round. ([#51](https://github.com/dasimon135/daikin_madoka/issues/51))

### The proxy shown as carrying a thermostat is now the one that really does

Home Assistant, not this integration, decides which Bluetooth proxy serves a connection: it keeps only the thermostat's address and re-picks a proxy by signal strength on every attempt. Everything this integration recorded about proxies was therefore an *intention*, not an observation.

Measured on four thermostats in a single restart: **three were connected through a different proxy than the one recorded.** That is why proxies already listed as paired kept asking to pair again — nothing was wrong with them, they had simply never carried the session the record claimed.

The **Connection source** sensor, the list of paired proxies and the sticky-proxy preference now all report what actually happened, and the lists repair themselves as real paths are observed. A failed pairing is only charged to a proxy when the attempt can actually name it; one that cannot name a proxy now blames nobody instead of guessing, so a working proxy can no longer be dropped for a failure it had no part in. ([#53](https://github.com/dasimon135/daikin_madoka/issues/53))

### Still open

Home Assistant may route a thermostat through a proxy it has never paired with. This release makes that **visible and harmless** — the connection is reported truthfully and no proxy is blamed for it — but it does not stop it happening. A thermostat that ends up in "pairing not completing" is telling you exactly that: confirm the pairing prompt on its screen once for that proxy, or set the proxy to `bluetooth_proxy: active: false`.

### Upgrading

Update via HACS and restart. Entity IDs and history are preserved. The bundled library moves to **pymadoka-ng 0.3.11** (installed automatically). Expect the list of paired proxies to grow on the first restarts: that is the integration recording paths it was previously blind to.

## v3.8.1 - July 2026

Requires **pymadoka-ng 0.3.10** (installed automatically). The library now states outright *why* pairing failed instead of leaving it to be inferred from a side effect, which is what v3.8.0 already reads when the attribute is there — this release simply makes it the version you actually run.

- **Re-pairing a thermostat now does something you can see.** When the retry cadence had been slowed to 15 minutes, walking to the thermostat and re-pairing it changed nothing for up to a quarter of an hour: the brake was still running and the recovery action looked inert. Pressing **Reconnect**, or submitting the re-pairing form, now lifts the brake, connects straight away and — if that attempt still fails — leaves the thermostat on its normal poll interval instead of putting it back under the 15-minute one. The warning disappears as soon as a connection actually succeeds, as before.
- **"Pairing not completing" no longer re-fires on the first slow round.** The integration keeps its own copy of the timed-out-round streak so it survives a rebuilt connection; it now spends that streak when the warning is raised, exactly as the library spends its own. Without that, every rebuilt connection started at the threshold and one more slow round was enough to raise the same verdict again. The 15-minute retry cadence and the warning itself are unchanged.

## v3.8.0 - July 2026

**A thermostat can no longer become unrecoverable.** This release rewrites the connection, pairing and recovery layer after a full review of it. Three field incidents drove it: a thermostat with a perfectly good bond was quarantined as "refused the pairing" and locked out; two others ended up in `setup_retry`, where *every* entity vanishes — including the **Reconnect** button the integration's own notification tells you to press; and neither could be re-paired by any documented route, because the 60-second "walk to the thermostat" budget was nested inside a 30-second connect budget and was silently cancelled at ~28 s.

### A pairing timeout is no longer treated as a refusal

A bond is per-proxy, and automatic reconnects only ever use proxies known to hold one. On such a path nobody has to touch the thermostat, so a pairing *timeout* there means congestion — not a lost bond. Only an explicit refusal now quarantines a device. A run of timeouts instead slows the retry cadence right down and raises a plain warning that says pairing is not completing, without accusing the thermostat of anything.

The pairing budgets were re-derived so this can actually be measured: each budget is now sized against the number of proxies that will be tried, so the attempt always fits inside the connect budget. Previously, with two or more proxies in range, no verdict could ever form at all. A retry-cadence brake also engages on repeated failure regardless of the verdict, so a thermostat that never answers is polled every 15 minutes rather than every minute.

### Recovery is always reachable

- A configured thermostat now **always loads**, degraded when it cannot connect, instead of disappearing into `setup_retry`. Its entities exist and read `unavailable` — the Reconnect button among them.
- A new **re-pairing flow** appears on the integration entry after a genuine refusal, with a Fix button, and works even when no entity is available. It reports success or failure instead of leaving you guessing.
- Every pairing flow — initial setup, Reconnect, re-pairing — now gets a real human-sized budget.
- The pairing window is bounded and always closes. A failed Reconnect used to leave it open forever, which quietly disarmed the quarantine and lifted the bonded-proxy restriction.
- The verdict and failure counter survive a restart, and are purged when the entry is removed.

### Seeing what is going on

- New **Connection status** sensor: `connected`, `retrying`, `pairing not completing`, `pairing required`, `not advertising`.
- **Connection source** is now enabled by default, and it and the signal-strength sensor stay available when the link is down — they read Home Assistant's own data and never needed the thermostat.
- Diagnostics no longer crash for a thermostat that never connected, and error messages no longer end in a bare colon.
- Proxies with no free connection slot are tried last, so a saturated proxy stops blocking a thermostat while others sit idle.
- Bonds are recorded as soon as pairing succeeds, and a proxy that keeps refusing is dropped — never the last one.
- Renaming a thermostat no longer wipes its list of bonded proxies.

### Madoka Card 0.7.0 → 0.7.1

The card no longer reports a successful reconnect that never happened: it shows a real pending state while the call is in flight, and a visible error if it fails. Hard-refresh your browser once after updating.

### ESPHome component — breaking change

The dual setpoint was hardcoded, so the ESP32 entity always exposed two temperatures regardless of the thermostat's range setting. It is now the `dual_setpoint:` option, **defaulting to a single setpoint**. If you rely on the dual UI — or call `climate.set_temperature` with `target_temp_low`/`target_temp_high` on an ESPHome Madoka entity — add `dual_setpoint: true` to the climate block and recompile. Home Assistant's own integration is unaffected; it already switched automatically.

## v3.7.1 - July 2026

**No more false "pairing required" quarantine on a Home Assistant restart.** Right after a restart the Bluetooth proxies are briefly congested, so a *valid* bond's SMP encryption runs slowly. The tight 8 s pairing budget could then time out and be misread as a lost bond, quarantining a thermostat that was never actually unbonded — typically one reached through a single, busy proxy. During the post-restart window the pairing budget is now widened (30 s) so a slow-but-valid bond has room to complete, then reverts to the tight budget once the device is back. A genuinely dead bond (an outright authentication rejection) still quarantines immediately, and the manual **Reconnect** window is unchanged.

## v3.7.0 - July 2026

### Madoka Card (0.7.0)

- **Reconnect where you see the problem**: when the thermostat goes unreachable, the card now surfaces its **Reconnect** button instead of leaving you with dead controls — a banner in the `full`/`compact` layouts, and in the `tile` layout in place of the inert `−`/`+` pair. It shows a "reconnecting…" state while the BLE link is re-established and disappears on its own once the device is back. Configurable with `reconnect: auto | always | never` (default `auto`), or point it at a specific entity with `reconnect_entity:`.
- The card finds the Reconnect button on its own from the thermostat's device — no configuration, and it is never confused with the *Reset filter* button (matched on the registry translation key, so a rename cannot break it).
- The `−`/`+`/power controls of the full layout are now disabled while the thermostat is unavailable.
- Bundled card bumped to 0.7.0 — hard-refresh the browser once after updating.
- Docs: the README now shows the card (light and dark screenshots, served per the reader's theme).

## v3.6.0 - July 2026

**A dead bond can no longer flood the thermostat** — fixes the slow-motion pairing storm v3.5.0 left open ([#41](https://github.com/dasimon135/daikin_madoka/issues/41)). When a proxy listed as bonded had actually lost its bond, every 600 s setup retry re-initiated an SMP exchange with an 8 s budget no human can meet — an endless salvo of prompts that eventually jams the thermostat.

- **The pairing-timeout streak survives entry retries.** The library's 3-round threshold was unreachable because Home Assistant rebuilds the connection on every retry and the counter restarted from zero; it now continues across rebuilds, so the refusal is actually concluded and the repair fires.
- **A concluded refusal suspends automatic reconnects indefinitely** instead of re-prompting a screen nobody is watching every 5 minutes. The device is left alone until you press **Reconnect** (which opens the 60 s pairing window) or a session succeeds; the suspension survives retries and reloads.
- **A suspended device still loads** in a degraded state, so the Reconnect button — the only remedy — actually exists. Previously the entry never finished setting up and the button never appeared.
- Repair text rewritten (en/fr/es); diagnostics expose `pairing_suspended`.
- Docs: dropped `CONFIG_BLE_SM_SC` / `CONFIG_BLE_SM_LEGACY` from the proxy guide — NimBLE symbols the Bluedroid stack never reads. A stock proxy only needs `io_capability: display_yes_no`.
- No dependency change (pymadoka-ng stays at 0.3.9).

[Full release notes](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.6.0)

## v3.5.0 - July 2026

**Pairing becomes a deliberate act** — fixes the root cause behind the phantom pairing prompts that v3.4.0 only partly contained. A proxy sitting closer to a thermostat wins the RSSI ordering and gets tried first on every reconnect while holding *no bond*, so each attempt began a real numeric-comparison pairing nobody could confirm in time — and those half-finished SMP exchanges jam the BRC1H.

- **Automatic reconnects can no longer start a pairing.** Every proxy that completes an authenticated session is recorded as bonded, and unattended reconnects are restricted to those, so they can only ever re-encrypt an existing bond. Entries predating the list fall back to their known preferred proxy; an install with nothing on record stays unrestricted so a first connect still works.
- **The reconnect button opens a pairing window**: pressing it means you are standing at the thermostat, so unbonded proxies become reachable and the pairing budget widens to 60 s — enough to compare the code and accept. The window closes on the next successful poll.
- **Pairing state survives the config-entry retry cycle** (moved to `hass.data`), so the backoff actually accumulates — on the coordinator it reset on every retry and never did.
- Requires **pymadoka-ng 0.3.9** (configurable pairing budget).

[Full release notes](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.5.0)

## v3.4.0 - July 2026

**No more phantom pairing prompts** — fixes a failure mode where the integration wrongly concluded a thermostat had lost its bond, put a pairing prompt on its screen, and by retrying every poll jammed its Bluetooth stack until it was toggled off and on by hand. Under the connection contention of a restart, the encryption handshake of an **already valid** bond routinely exceeded its timeout, and a timeout was treated as proof the bond was gone.

- **A pairing timeout is no longer proof of a missing bond** (via pymadoka-ng 0.3.8): it is treated as ambiguous and retried, and the pairing error is only reported after several consecutive rounds in which *every* path timed out. An explicit authentication rejection still reports immediately.
- **Connects are serialized across devices**, so thermostats stop competing for the proxies that serve them. The lock is only held around a reconnect, never around a normal poll.
- **A pairing refusal now backs off** (60 s, doubling, capped at 5 minutes) instead of re-attempting every poll. The reconnect button bypasses the backoff entirely.

[Full release notes](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.4.0)

## v3.3.0 - July 2026

Quality release: easier reconfiguration, better observability, and a big step up in internal quality (typing, CI, test coverage).

- **Reconfigure flow**: rename a thermostat or change its MAC address from the entry's ⋮ menu — no more delete + re-add. A MAC change runs the full authenticated connection test before being accepted.
- **New "Connection source" diagnostic sensor** (disabled by default — enable it on the device page): shows which Bluetooth proxy each thermostat is currently connected through. In multi-proxy homes this answers the #1 debugging question at a glance.
- **Richer diagnostics download**: now includes coordinator health (last update success, failure count, update interval, active repair flags and the resolved preferred proxy) — much more useful bug reports.
- **Fixes**: removed a latent `HVACMode.OFF → AUTO` mapping that could have silently switched the unit to AUTO; README entity list updated (operating time sensor, reconnect button).
- **Internal quality**: migration to `ConfigEntry.runtime_data`, full type annotations with mypy in CI, test coverage measured in CI (40% → 81%, climate 90%), Dependabot, pinned CI actions, pre-commit hooks.

## v3.2.0 - July 2026

Validated on hardware (4 thermostats, 4 proxies) — including a live replay of
the incident that motivated this release.

Multi-proxy robustness: reliable in homes with several Bluetooth proxies and several thermostats.

- **Sticky proxy**: the integration remembers which proxy last authenticated with each thermostat and tries it first on every reconnect. A closer proxy that has no bond can no longer steal the connection and take the thermostat down with `Insufficient authentication`.
- **`pairing_required` repair**: when every connection path refuses the link for lack of a bond, an actionable repair appears that **names the refusing proxies**, so you know exactly which one to pair (or set passive) — see the new [ESPHome proxy reference](docs/esphome-proxy.md).
- **Stale-value grace**: 1–2 transient poll failures no longer punch holes in graphs or flicker entities to unavailable — sensors keep their last value for a short grace period while the connection recovers. Real outages (and pairing failures) still surface immediately.
- **Saner discovery & onboarding**: discovery ignores advertisements below −90 dBm (no more discovery cards for out-of-home devices at the edge of range), and the config flow now tests the connection — including pairing — before creating the entry, so a misconfigured setup fails in the flow instead of producing a dead device.
- **Registry hygiene**: orphaned devices left behind by removed entries are cleaned up at startup, and devices can now be deleted individually from the device page.
- Requires **pymadoka-ng 0.3.7** (typed connection errors, candidate proxy list, preferred-proxy support; 0.3.7 additionally makes each connection attempt a single path decision, so a mid-retry failover can no longer hand the device to an unbonded proxy; installed automatically).

## v3.1.1 - July 2026

- **Config flow**: manual setup ("Add integration") now takes over a pending
  Bluetooth discovery flow for the same device instead of aborting with
  `already_in_progress`; the discovery card is dismissed automatically once
  the entry is created.
- **Tests/CI**: first pytest suite (Home Assistant test harness) wired into
  the CI workflow alongside ruff.

## v3.1.0 - July 2026

### Madoka Card

- **Tap-to-open on the tile layout**: tapping a `tile` row now opens the **full dial card in a popup** — a self-contained modal overlay (close on ✕, scrim click, or Escape), with no external dependency (browser_mod not required). It stays theme-aware and localized, and updates live. Set `tile_tap: more-info` to open Home Assistant's native more-info dialog instead. Keyboard accessible (Enter/Space, focus ring).
- Bundled card bumped to 0.6.1 — hard-refresh the browser once after updating.

## v3.0.1 - July 2026

- **Madoka Card**: the **tile** layout now keeps the ambient temperature visible when the unit is off (e.g. `23° · Off`) instead of showing only the "Off" label, so the row stays informative in a dense grid. Bundled card bumped to 0.5.2 — hard-refresh the browser once after updating.

## v3.0.0 - July 2026

A first-class dashboard experience, guided recovery, and self-healing reliability.

### Madoka Card (bundled)

- A dial-style Lovelace card shipped **inside the integration** (auto-registered, no separate install) that mirrors the physical BRC1H: a state-colored glowing halo, a setpoint arc, fan segments, `−`/`○`/`+` controls, a mode switcher, an eye-brightness slider, a 12 h temperature sparkline and filter/signal chips.
- Three layouts: **full** (default), **compact**, and an ultra-compact **tile** row (`layout: tile`) that lines up with HA's tile cards in a dense grid.
- Zero config — it auto-discovers the sibling entities (outdoor temperature, brightness, filter, signal) from the same device; you only give it the `climate.*` entity. Theme-aware, and internationalized (mode names from HA's own climate translations; card words in en/fr/es/de/it/nl).

### Setup & recovery

- **Repair issue**: after several consecutive failed polls, an actionable *device unreachable* repair appears (linking the pairing/proxy documentation) and clears itself on recovery — turning a silent unavailable device into a fixable message.
- **Reconnect button** (diagnostic, always available): drops and re-establishes the Bluetooth link on demand.

### Reliability & stats

- **Self-healing reconnect fixed** (requires **pymadoka-ng 0.3.5**): re-pairing now happens on every reconnect. Because the bond is stored per Bluetooth proxy, a dropped link — or the Reconnect button — used to fail with `Insufficient authentication`; it now recovers cleanly. Validated on hardware.
- **Adaptive polling**: a command triggers an immediate refresh plus a short follow-up, so the UI reflects the device applying the change without waiting a poll cycle.
- **Operating-time sensor**: cumulative hours the unit has been powered on, persisted across restarts.
- Brand icon ships locally (displayed on HA ≥ 2026.3); entity icons via `icons.json`, including a state-aware filter alert.

### Upgrading from 2.4.x

Update via HACS and restart. Entity IDs and history are preserved (a couple of new diagnostic entities appear). The bundled library moves to **pymadoka-ng 0.3.5** on PyPI (installed automatically). The Madoka Card resource is registered automatically — hard-refresh the browser once.

## v2.4.0 - July 2026

### HA Integration — "Modern Bluetooth" release

- **ESPHome Bluetooth proxy support**: connections now go exclusively through Home Assistant's Bluetooth stack (`bleak-retry-connector`), so the thermostat can be reached through any ESPHome Bluetooth proxy — the HA server no longer needs to be within BLE range. The Linux-only `bluetoothctl` shell-out at setup is gone (`force_update` entry option is now ignored).
- **Automatic discovery**: BRC1H thermostats advertised near HA are discovered and offered in the UI (matched on the Madoka BLE service UUID — the units advertise the local name "Daikin", verified on hardware). The manual flow now shows a dropdown of discovered devices with a free-MAC fallback.
- **DataUpdateCoordinator**: one shared BLE poll per device instead of independent per-entity updates; entities become unavailable when polling fails; setup raises "not ready" (with automatic retry) when the device is unreachable.
- **Dual setpoint in AUTO mode**: when the device reports `range_enabled`, the climate entity exposes separate heating/cooling target temperatures (`TARGET_TEMPERATURE_RANGE`).
- **Device-reported temperature limits**: `min_temp`/`max_temp` are read from the thermostat's own setpoint limits when available (fallback 16–32 °C).
- **New entity**: Bluetooth signal strength (RSSI) diagnostic sensor, disabled by default.
- **Options flow**: configurable poll interval (10–600 s, default 60 s).
- **Diagnostics**: downloadable config-entry diagnostics with MAC redaction.
- **Device registry**: model, hardware and software versions from the device info characteristics.
- **Modern entity naming** (`has_entity_name` + translation keys) and a **French translation** (en/es/fr). Entity IDs and unique IDs are unchanged; displayed names may differ slightly.
- **Brand icon**: a BRC1H-inspired icon ships inside the integration (`brand/` folder, displayed on HA ≥ 2026.3) — no more "icon not available" placeholder on discovery cards; entity icons move to `icons.json` with a state-aware filter alert.
- **Self-healing polling**: every poll cycle re-establishes the BLE connection if it dropped (or aborted), so a transient failure no longer requires reloading the integration.
- **Errors surface in the UI**: failed commands (set temperature, mode, fan, etc.) now raise a visible Home Assistant error instead of silently reverting on the next poll.
- **Setpoint writes no longer clobber device settings**: updates echo the thermostat's own range mode and configured limits back instead of resetting them (long-standing pymadoka behavior, fixed in 0.3.0).
- **MAC normalization**: manually entered addresses are normalized to the canonical form, fixing "device not found" loops for `aa-bb-...` style input and preventing duplicate entries from discovery.
- **Proxy pairing (validated on hardware)**: pymadoka now pairs explicitly (MITM) before subscribing to notifications — the BRC1H silently ignores unauthenticated clients, which is why proxied connections used to hang with no response. The proxy itself needs a small YAML addition (io_capability + a numeric-comparison responder); see the README's Requirements section.
- **pymadoka-ng 0.3.2** (dist renamed; import stays `pymadoka`): modern pyproject packaging (lean core: `bleak` + `bleak-retry-connector`; CLI/MQTT moved to extras), unit tests + CI, explicit `pair()` + settle delay before the first command, per-feature query retry, fix for a hang when the device was out of range at setup (swallowed task cancellation), proper cancellation propagation in the send path, and orphan-reconnect prevention on unload. The dist rename also works around HA never re-installing a git requirement whose package is already present.
- Version bumped to 2.4.0; requires **pymadoka-ng 0.3.4 from PyPI** (`pymadoka-ng==0.3.4`, no longer a git requirement — HA now reinstalls cleanly on every version bump).
- **Upgrading from ≤ v2.3.x**: the old `pymadoka` Python dist may remain installed alongside `pymadoka-ng` (they share files). Harmless day to day, but avoid `pip uninstall pymadoka` inside the HA container — it would delete shared files. The situation resolves itself at the next HA Core update (fresh container).

No ESPHome changes. ESPHome users should keep `ref: v2.1.1`.

---

## v2.3.0 - Juin 2026

### HA Integration

- **Correctif `bleak`** : la lib `pymadoka` n'importe plus `discover` (supprimé dans bleak 0.20). L'intégration HA native fonctionne désormais sur les versions récentes de Home Assistant — l'option ESPHome n'est plus un contournement obligatoire. Merci à [@andreaippo](https://github.com/andreaippo) (PR #13 + pymadoka #30).
- **Chemin Bluetooth HA** : connexion via la pile Bluetooth de Home Assistant (`bleak_retry_connector`) avec reconnexion à backoff exponentiel ; ajout de `dependencies: ["bluetooth"]`. Le chemin standalone/CLI de pymadoka reste fonctionnel (`hass=None`).
- **Robustesse** : verrou par opération + timeout de 10 s sur chaque commande, garde anti-réentrance sur `start()`, `cleanup()` plus sûr.
- **Config flow par appareil** : un thermostat par entrée (`address` + `friendly_name`), `unique_id` = MAC. Corrige le blocage du flow avec plusieurs adresses MAC. Les entrées existantes (liste `devices`) restent prises en charge (rétro-compatibilité, pas de re-création nécessaire).
- **Nouvelle entité** : `number` pour la luminosité de la LED (eye brightness), 0–19 — parité avec le composant ESPHome.
- **Correctifs** : `async_unload_entry` libère désormais correctement les ressources (`stop()` des controllers, retour de l'état) ; `sensor` utilise `native_unit_of_measurement` ; `hacs.json` corrigé.
- Version bumped to 2.3.0 ; pymadoka 0.2.16.

No ESPHome changes. ESPHome users should keep `ref: v2.1.1`.

---

## v2.2.0 - Juin 2026

### HA Integration

- **Nouvelle entité** : `binary_sensor` pour l'alerte filtre (`device_class: problem`)
- **Nouvelle entité** : `button` pour réinitialiser le compteur filtre (`entity_category: diagnostic`)
- **Restructuration** : fichiers déplacés vers `custom_components/daikin_madoka/` (standard HACS — installation transparente pour les utilisateurs existants)
- **Parité ESPHome** : l'intégration HA directe expose désormais les mêmes entités filtre que le composant ESPHome
- Version bumped to 2.2.0

No ESPHome changes. ESPHome users should keep `ref: v2.1.1`.

---

## v2.1.1 - Avril 2026

### Fixes

- **ble_client**: declare `synchronous=True/False` on all 6 `register_action()` calls — removes ESPHome 2026.4 warnings about missing `synchronous=` parameter
- **madoka**: both `esphome/components/` and `esphome_components/` copies now use `add_feature_flags()` consistently — removes remaining `-Wdeprecated-declarations` compiler warnings

No behaviour change. Thanks to [@Dvorf](https://github.com/Dvorf) for identifying both issues.

---

## v2.1.0 - Avril 2026

### ESPHome

- **Nouvelles entités** : `outdoor_temperature`, `clean_filter`, `firmware_version`, `eye_brightness`, `reset_filter` exposées par le composant madoka
- **Suppression du `ble_client` local** : ESPHome 2026.4 gère nativement la gestion des connexions BLE, le composant local n'est plus nécessaire
- **Correction deprecations ESPHome 2026.4** : `ClimateTraits.set_supports_current_temperature()` et `set_supports_two_point_target_temperature()` remplacés par `add_feature_flags()`
- **Correction `AUTO_LOAD`** : ajout des dépendances `binary_sensor`, `button`, `number`, `sensor`, `text_sensor` dans `climate.py`
- **Script stop/start BLE amélioré** : ajout de `ble_client.disconnect` explicite en plus de `stop_scan()` pour libérer correctement le thermostat lors du ré-appairage téléphone
- **Reconnexion conditionnelle** : le `on_disconnect` ne relance la connexion que si le switch proxy est actif

### Compatibilité

Requiert **ESPHome 2025.10+**. Testé et validé sur ESPHome 2026.4.0.

---

## v2.0.0 - Octobre 2025

### Ajouts

- Composants ESPHome dans `esphome_components/madoka/`
- Support ESP32-S3 (M5Stack Atom S3 Lite)
- Documentation complète (README, exemple de configuration)
- Intégration HA directe (existante, inchangée)

### Crédits

- Intégration HA originale : [@mduran80](https://github.com/mduran80/daikin_madoka)
- Composant ESPHome madoka : [Petapton/esphome](https://github.com/Petapton/esphome)
- Support ESP32-S3 et switch ré-appairage : [@Quev1n](https://forum.hacf.fr)

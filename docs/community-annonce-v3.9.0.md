# community.home-assistant.io announcement — v3.9.0

> Post in the existing integration thread:
> https://community.home-assistant.io/t/control-daikin-madoka-brc1h-thermostat-via-bluetooth-ha-custom-integration-esphome-component/984675

---

## Daikin Madoka v3.9.0 — the proxy shown as carrying a thermostat is now the one that really does

[v3.9.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.0) fixes **two ways a perfectly healthy thermostat could be declared "pairing required"** and stay locked out until somebody physically walked to it.

What is different this time: nothing here was reasoned about. Both bugs were **measured on real hardware**, and the fix was verified the same way.

### Bug 1 — the thermostat that was working two minutes ago

On my own setup, 7 August at 22:10: Home Assistant restarts. At **22:11:44** the living-room unit connects and polls successfully — valid bond, authenticated session, all good. At **22:14:00** it is declared *pairing required*, automatic reconnects stop, and it stays dead for **11 hours** until I press its screen.

But a thermostat that authenticates at 22:11:44 has not lost its bond on two separate proxies at 22:14:00.

The reason: refusals are recognised from the **error text** (`insufficient authentication`, ATT `error=5`). The BRC1H accepts only one central at a time, so a stale proxy link — or simply every thermostat reconnecting at once after a restart — produces **exactly the same message** as a bond that is genuinely gone.

**Now**: a refusal arriving within 10 minutes of a completed, authenticated session is treated as congestion. Reconnects slow to one every 15 minutes and a warning appears, but **nothing is quarantined and nobody is summoned to the thermostat**. One good session excuses one refusal — a bond that really is dead still surfaces on the next round.

### Bug 2 — the recorded proxies were fiction

This one runs deeper, and it probably explains a few things on your setup too.

**The integration does not choose the proxy.** Home Assistant keeps only the thermostat's address and re-picks a proxy by signal strength on *every* attempt. So everything the integration recorded about proxies was an **intention**, never an observation.

I added a temporary probe to read what the real Bluetooth stack holds. In a single restart, **3 of 4 thermostats were served by a different proxy than the one recorded**:

| Thermostat | Recorded | Real path |
|---|---|---|
| Bedroom 1 | Proxy A | **Proxy B** |
| Bedroom 2 | Proxy A | **Proxy C** |
| Living room | Proxy C | **Proxy B** |
| Bedroom 3 | Proxy C | Proxy C |

That is what explains **repeated pairing prompts from proxies already listed as bonded**: nothing was wrong with them, they had simply never carried the session the record claimed. And a refusal could be charged to — even cost its bond to — a proxy that took no part in the round.

**Now**: the **Connection source** sensor, the list of bonded proxies and the sticky-proxy preference all report what actually happened, and the lists **repair themselves** as real paths are observed. A failed pairing is only charged to a proxy when the attempt can actually name it; otherwise nobody is blamed, instead of guessing.

### Still open — worth knowing

Home Assistant can still route a thermostat through a proxy it has never paired with. v3.9.0 makes that **visible and harmless** (the connection is reported truthfully, no proxy is wrongly accused), but it does **not** prevent it.

In practice: if a thermostat sits in **"pairing not completing"**, that is exactly what it is telling you. The remedy is unchanged — confirm the pairing prompt on its screen **once for that proxy**, or set the proxy to `bluetooth_proxy: active: false`.

An example seen live right after deploying, on one of my units:

```
did not complete pairing ... (tried via: D0:CF:13:0F:11:F6, D0:CF:13:0F:11:F6)
```

**The same proxy twice in one round.** Before v3.9.0 this log would have shown two different addresses and given the impression that two paths had been tried. It is direct proof that iterating over several candidates does not try several paths: Home Assistant returns the same winner every time.

### Upgrading

Via HACS (custom repository `dasimon135/daikin_madoka` if you haven't added it), then restart. Entity IDs and history are preserved, and there is nothing to reconfigure. Requires **pymadoka-ng 0.3.11**, installed automatically, so the restart takes a little longer than usual.

Don't be surprised if your list of bonded proxies **grows** over the first few restarts: that is the integration finally recording paths it was blind to.

Feedback welcome here or on [GitHub](https://github.com/dasimon135/daikin_madoka/issues)!

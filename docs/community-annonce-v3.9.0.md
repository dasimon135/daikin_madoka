# community.home-assistant.io announcement — v3.9.0

> Post in: https://community.home-assistant.io/t/control-daikin-madoka-brc1h-thermostat-via-bluetooth-ha-custom-integration-esphome-component/984675
> Do not paste this header — the post starts after the rule below.

---

## Daikin Madoka v3.9.0

[v3.9.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.0) fixes **two ways a perfectly healthy thermostat could be declared "pairing required"** and stay locked out until somebody walked to it. Both were measured on real hardware rather than reasoned about.

**1. The thermostat that was working two minutes ago.** On my setup: connected and polling at 22:11:44, declared *pairing required* at 22:14:00, dead for 11 hours. Refusals are recognised from the error text, and the BRC1H accepts only one central — so a stale link, or simply every thermostat reconnecting at once after a restart, produces the same message as a bond that is genuinely gone. Now, a refusal arriving within 10 minutes of a successful authenticated session is treated as congestion: slower retries, a warning, but **no quarantine and nobody summoned to the thermostat**.

**2. The recorded proxies were fiction.** The integration does not choose the proxy: Home Assistant keeps only the thermostat's address and re-picks by signal strength on *every* attempt. So everything recorded about proxies was an intention. In a single restart, measured with a probe:

| Thermostat | Recorded | Real path |
|---|---|---|
| 1 | Proxy A | **Proxy B** |
| 2 | Proxy A | **Proxy C** |
| 3 | Proxy C | **Proxy B** |
| 4 | Proxy C | Proxy C ✅ |

**3 out of 4.** That is what explains **pairing prompts from proxies already listed as bonded**: nothing was wrong with them, they had simply never carried the session the record claimed. The *Connection source* sensor, the bonded-proxy list and the sticky-proxy preference now report reality, and the lists repair themselves.

### Still open

Home Assistant can still route a thermostat through a proxy it has never paired with. v3.9.0 makes that **visible and harmless**; it does not prevent it. If a thermostat sits in **"pairing not completing"**, that is exactly what it is telling you: confirm the pairing prompt on its screen **once for that proxy**, or set the proxy to `bluetooth_proxy: active: false`.

### Upgrading

Via HACS, then restart. Entity IDs and history are preserved, nothing to reconfigure. Requires **pymadoka-ng 0.3.11** (installed automatically), so the restart takes a little longer. Don't be surprised if your bonded-proxy list grows over the first few restarts: that is the integration finally recording paths it was blind to.

Feedback welcome here or on [GitHub](https://github.com/dasimon135/daikin_madoka/issues)!

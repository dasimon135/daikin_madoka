# community.home-assistant.io announcement — v3.2.0

> Post in the existing integration thread (or "Share your Projects!" if none).

---

## Daikin Madoka (BRC1H) v3.2.0 — multi-proxy robustness: no more thermostats dying without explanation

Hi all,

[v3.2.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.2.0) of the **Daikin Madoka (BRC1H)** custom integration is out. This release is entirely about **reliability in homes with several Bluetooth proxies and several thermostats** — hardware-validated on a 4-thermostat / 4-proxy installation, including a live replay of the exact failure that motivated the work.

### The problem it fixes

The BRC1H stores its Bluetooth pairing **per proxy** (one bond per ESPHome node), but Home Assistant routes connections through whichever proxy has the **strongest signal**. When those two disagree — a nearby proxy that was never paired wins the pick — the thermostat refuses the link (`Insufficient authentication`)… and all you used to see was an `unavailable` entity with zero explanation.

### What's new

- **Sticky proxy**: the integration remembers which proxy last authenticated with each thermostat and tries it first on every reconnect. An unbonded closer proxy can no longer steal the connection and take the thermostat down.
- **`pairing_required` repair**: when every path refuses the link for lack of a bond, an actionable repair appears that **names the refusing proxies**, so you know exactly which one to pair (or set to passive). It clears itself on recovery.
- **Pairing codes as HA notifications**: the new [ESPHome proxy reference](https://github.com/dasimon135/daikin_madoka/blob/main/docs/esphome-proxy.md) ships pairing responders that **push the 6-digit numeric-comparison code to Home Assistant** the moment the prompt appears on the thermostat screen. With several thermostats you finally know *which* unit is pairing through *which* proxy — just match the code on the physical screen.
- **No more graph holes**: 1–2 transient poll failures no longer flip entities to unavailable — sensors keep their last value for a short grace period while the connection recovers. Real outages (and pairing failures) still surface immediately.
- **Saner discovery & onboarding**: advertisements below −90 dBm no longer produce discovery cards (no more your neighbour's BRC1H showing up), and the config flow now performs a **full authenticated connection test before creating the entry** — a config entry stuck forever in `setup_retry` is no longer possible.
- **Registry hygiene**: orphaned devices left behind by removed entries are purged at startup; devices can be deleted individually from the device page.
- Requires **pymadoka-ng 0.3.7** (installed automatically).

### The golden rule (multi-proxy homes)

If several active proxies are in range: **every active proxy must be paired with each thermostat** (the prompt appears once per proxy — now with the code in your notifications), otherwise set the proxy to `bluetooth_proxy: active: false`. Full YAML and a troubleshooting table are in the [ESPHome proxy reference](https://github.com/dasimon135/daikin_madoka/blob/main/docs/esphome-proxy.md).

### Upgrading

Via HACS (custom repository `dasimon135/daikin_madoka` if you haven't added it yet), then restart. Entity IDs and history are preserved. A pairing prompt may appear on a thermostat after the restart — that's a proxy regularizing its bond: confirm it once and you're set.

Feedback welcome here or on [GitHub](https://github.com/dasimon135/daikin_madoka/issues)!

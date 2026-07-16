# community.home-assistant.io announcement — v3.0.0 (English)

---

## 🚀 Daikin Madoka (BRC1H) v3.0.0 — a dial card, guided recovery & rock-solid reconnection

Hi everyone,

**[v3.0.0](https://github.com/dasimon135/daikin_madoka/releases/tag/v3.0.0)** of the Daikin **BRC1H "Madoka"** custom integration is out. After 2.4.0 ("Modern Bluetooth"), this release is all about the dashboard experience and reliability — validated on real hardware.

**Repo:** https://github.com/dasimon135/daikin_madoka

### 🎛️ The Madoka Card

The headline feature: a **dial-style Lovelace card shipped inside the integration** (auto-registered, no separate install). It mirrors the physical BRC1H — a **glowing halo that changes color with the mode**, a setpoint arc, fan segments, `−`/`○`/`+` controls, a mode switcher, an eye-brightness slider, a 12 h temperature sparkline and filter/signal chips.

Three layouts:

```yaml
type: custom:madoka-card
entity: climate.my_madoka
# layout: full | compact | tile
```

- **full** — the complete dial
- **compact** — dial + controls
- **tile** — an ultra-compact row that lines up with HA's native tile cards in a dense grid

It **auto-discovers** the related entities (outdoor temperature, brightness, filter, signal) from the same device — you only give it the `climate.*` entity. It follows your **theme** and **language** (mode names from HA's own climate translations; card words in en/fr/es/de/it/nl).

### 🔧 Guided recovery

- A **repair issue** ("device unreachable", with a link to the pairing/proxy docs) appears when the connection fails for a while and clears itself on recovery — no more silent *unavailable* device.
- A **Reconnect button** (diagnostic) to force a link re-establishment on demand.

### 🔄 Self-healing reconnect fixed

A real underlying bug was caught and fixed: after a BLE drop, reconnection could fail with `Insufficient authentication` (the bond is stored **per Bluetooth proxy**). The integration now **re-pairs on every reconnect**, so it recovers cleanly. Validated live on hardware. (Requires pymadoka-ng 0.3.5, installed automatically.)

### ⚡ Also

- **Adaptive polling** — a command is reflected in the UI immediately, without waiting a poll cycle.
- An **operating-time** sensor (cumulative powered-on hours, persisted across restarts).

### ⚠️ Bluetooth proxy reminder

Unchanged since 2.4.0: the BRC1H requires **authenticated (MITM) pairing**. The stock bluetooth-proxy firmware can't do it — a few lines in the proxy YAML, all documented in the [README Requirements section](https://github.com/dasimon135/daikin_madoka#requirements).

### ⬆️ Upgrading

Update via HACS and restart. Entity IDs and history are preserved; the library moves to **pymadoka-ng 0.3.5** on PyPI (installed automatically). Hard-refresh the browser once for the card.

*(Not in the HACS default store yet — add `https://github.com/dasimon135/daikin_madoka` as a custom repository, category Integration, in the meantime.)*

Feedback and testers very welcome — here or on [GitHub Issues](https://github.com/dasimon135/daikin_madoka/issues). Enjoy your local Madoka control! ❄️🔥

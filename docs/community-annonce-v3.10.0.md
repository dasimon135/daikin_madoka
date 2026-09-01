# community.home-assistant.io announcement — v3.10.0

> Post in: https://community.home-assistant.io/t/control-daikin-madoka-brc1h-thermostat-via-bluetooth-ha-custom-integration-esphome-component/984675
> As a reply in the same thread as the v3.9.2 announcement.
> Do not paste this header — the post starts after the rule below.

---

**v3.10.0 is out. If your thermostat has been lighting up a 6-digit code on its own, with nobody asking it to, that is what this release is about.**

### What was happening

Home Assistant picks which Bluetooth path to reconnect through, and it re-picks on every attempt. So it could route through a proxy that had never been paired with that thermostat. When it did, pairing started, and the thermostat put a 6-digit confirmation code on its screen waiting for a human. At 3 a.m., nobody confirms it.

None of this showed up in Home Assistant — the entity stayed available, because the next attempt went through a good path. The only evidence was a lit screen in the living room.

Measured on my own install: **one thermostat produced eight of those prompts in a single hour**, every one through a proxy it should never have used.

One counter-intuitive detail: this is not "closest proxy wins". In the case I measured, the elected proxy was **15 dB weaker** than a properly bonded one that was available. It won because Home Assistant also weighs free connection slots and recent failures. Any proxy in range can be picked at any time.

### What changed

The check moved to where it actually bites: **after the link is up, just before pairing**, and against the path that was really used rather than the one that was offered. If that path is not sanctioned, the connection is closed with no pairing exchange at all. Nothing appears on the thermostat.

A proxy that has lost its keys is now also dropped from the trusted list, instead of being retried forever.

### A new repair, for the one case only a human can fix

If Home Assistant keeps choosing an unsanctioned path three rounds in a row, you will get a **repair** in Home Assistant. It names the proxy the connection keeps landing on, and offers the re-pairing flow.

In other words: instead of pestering the thermostat, the integration tells you which proxy to go and pair with.

It is a warning, not an error. No pairing was refused — none was attempted.

### What it costs, stated plainly

This is a real cost and I would rather say it than have you discover it.

If Home Assistant elects an unsanctioned path on *every* attempt of a round, the integration refuses them all, and the thermostat goes **unavailable**.

That is the deliberate trade: a thermostat that is unavailable, with a repair telling you what to do, beats one that works while lighting a 6-digit code nobody answers, every night, invisibly.

Measured here on 2026-08-29: unavailable from 16:29, back on its own at 16:52 when the proxy scoring shifted — no pairing, no prompt, nothing for me to do. So it lasts minutes to hours, and **the Reconnect button ends it whenever you want**.

### Two unrelated fixes that were reported in this thread

**The card that vanishes.** The `custom element doesn't exist: madoka-card` message, with no pattern anyone could pin down. The card file was only served once the thermostats had been polled, several seconds after Home Assistant started answering requests. Opening a dashboard right after a restart landed squarely in that window: the browser got an error page instead of JavaScript, and the card stayed broken until it was reloaded by hand. The card is now served as soon as the component loads, before anything else.

**The device page is finally filled in.** Model and firmware never made it there, on every install — they were read before the Bluetooth link even existed. They are now read after a poll that has actually succeeded.

⚠️ With one surprise, found by reading real hardware: what the BRC1H publishes there describes **its radio module** (a "UE878 RF MODULE" by Universal Electronics), not the Daikin controller. So the "model 0.1" you will see belongs to the radio. The Daikin firmware version is not exposed over Bluetooth at all.

### Updating

Through HACS, then restart. Nothing to reconfigure, no re-pairing needed.

**ESPHome component users: nothing to do this time.** Unlike v3.9.2, this release does not touch the component, so there is no need to rebuild.

### While I have your attention — the host adapter

Three people have now reported the same thing in this thread and on GitHub: pairing a BRC1H from **Home Assistant's own Bluetooth adapter** connects, shows the passkey, and then drops the link seconds later. In at least one case the plain `bluetoothctl` procedure fails exactly the same way, with no integration involved at all.

To be straight about it: **the ESPHome proxy route is the one validated on hardware here. The host-adapter route is not.** It works for some people and I have not been able to reproduce or support the cases where it does not. The README documents both without saying so clearly enough, and I will fix that.

If you are stuck on a local adapter or dongle, the ESPHome proxy is the answer I can actually stand behind. If you would rather keep digging, open an issue with a `btmon -w` capture across one pairing attempt, your BlueZ version and your adapter chipset — those three are the only things that would identify where the link dies.

Release notes: https://github.com/dasimon135/daikin_madoka/releases/tag/v3.10.0

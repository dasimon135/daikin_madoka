# community.home-assistant.io announcement — v3.11.0

> Post in: https://community.home-assistant.io/t/control-daikin-madoka-brc1h-thermostat-via-bluetooth-ha-custom-integration-esphome-component/984675
> As a reply in the same thread as the v3.10.0 announcement.
> Do not paste this header — the post starts after the rule below.

---

**v3.11.0 is out. Both new features in it were written by other people, which is a first for this project.**

> ⚠️ **If you run the ESPHome component on a dedicated ESP32, you have one line to change before rebuilding.** Jump to the ESPHome section below. If you use the Home Assistant integration with Bluetooth proxies — which is most of you — there is nothing to do beyond the usual HACS update.

### Ventilation units are supported — VAM and HRV

Thanks to **@Frank802**, who wrote this, tested it on his own unit, and stayed patient through a long review.

Until now the integration assumed everything behind a BRC1H was an air conditioner. A VAM only ventilates: it has no setpoint, no heating, no cooling. It technically worked, in the sense that it connected and then showed you a thermostat interface that made no sense for it.

Now it is a device type of its own. When you add a unit — or through **Reconfigure** on an existing one — you pick **Appliance type: Ventilation only (VAM / HRV)**, and you get:

- **Off** and **Fan only**, and nothing else in the mode list
- **Low** and **High** fan speeds
- the three ventilation modes as presets: **Auto**, **Heat exchange**, **Bypass**
- no setpoint controls, because there is nothing to set

On the ESPHome side there is a matching `madoka_vam` component.

**Being straight about it: I do not own a VAM.** Everything above was measured by Frank802 on his. What I verified here is the other half — that a BRC1H thermostat still behaves exactly as before, because this release also moves code the two device types share. If you have a VAM and something is off, please open an issue; you will be telling me something I cannot see from here.

### Energy consumption sensors

Thanks to **@sharkoz**, who found that the Madoka keeps consumption counters of its own and wrote the code to read them.

Six sensors: **today, yesterday, this week, last week, this year, last year**. They are **off by default** — turn on *Read energy consumption* in the integration's options. "Energy today" is the one built for the **Energy dashboard**; the other five are plain read-outs. Today refreshes every five minutes, the other periods once a day.

**Not every unit has those counters.** Mine do not: all four of my BRC1H answer that query with an empty value. That is not a failure and is no longer treated as one — the integration says so once in the log, switches energy polling off for that thermostat, and leaves every other reading alone. So if you enable the option and the sensors stay empty, your indoor unit simply does not count. Nothing else breaks.

### ESPHome: one transport instead of two copies — and one line for you to add

The `madoka_vam` component arrived carrying a full copy of `madoka`'s Bluetooth layer: seven functions identical character for character, four more differing only by a log label. That could only end one way — a fix landing in one copy and quietly rotting in the other, with CI unable to notice, because it compiles these components and never runs them.

The shared part now lives once, in a new component called `madoka_base`. 1398 lines became 1225. The polling order is unchanged: same commands, same delays, same sequence, on both components.

**What you have to do.** ESPHome only copies the external components you name, and it cannot pull in one that is not listed. So `madoka_base` has to be spelled out:

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka, madoka_base ]      # add madoka_vam too if you have a VAM
```

Then rebuild. If you forget, the build stops on a missing component — it is a loud failure, not a silent one, and it cannot produce a half-updated firmware.

Also gone: the copy of `ble_client` this repo had been carrying. It turns out it was never compiled — every config here and in the docs lists `components: [ madoka ]`, so ESPHome always used its own. Removing it changes nothing in the firmware you get. Everything it once existed for has been upstream since ESPHome 2026.7.2. The pinned ESPHome moves to 2026.8.2 in passing.

### Updating

Through HACS, then restart. Nothing to reconfigure, no re-pairing.

**ESPHome component users:** update the components, add `madoka_base` to the `components:` list as above, rebuild.

Release notes: https://github.com/dasimon135/daikin_madoka/releases/tag/v3.11.0

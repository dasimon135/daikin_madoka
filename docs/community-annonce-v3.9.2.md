# community.home-assistant.io announcement — v3.9.2

> Post in: https://community.home-assistant.io/t/control-daikin-madoka-brc1h-thermostat-via-bluetooth-ha-custom-integration-esphome-component/984675
> As a reply in the same thread as the v3.9.1 announcement.
> Do not paste this header — the post starts after the rule below.

---

**v3.9.2 is out, and it fixes something v3.9.1 broke.** If you updated two days ago and your temperature has been landing a degree too high, this is why.

v3.9.1 made setpoint writes work on single-setpoint units by filling both registers of the frame with the target. That is right for a controller that reports a minimum differential of zero — it stores the equal pair as sent, which is what @mauriziofanetti-hue's units and mine do.

Some BRC1H report a **non-zero minimum differential**: they keep at least one degree between the cooling and heating setpoints, and they cannot hold an equal pair at all. The frame carries cooling before heating, so applying heating breaks the gap and the controller restores it by pushing the register it already applied — cooling — up one. Ask for 26 and you get 27. Ask for one degree less and nothing appears to happen, because the correction puts it straight back.

The written pair now carries the controller's own minimum differential instead of assuming zero. On a unit reporting zero the frame is byte for byte what v3.9.1 sent, so nothing changes for the units that report is about.

@speynaud found it, produced the diagnostics and the traces that identified the field, and then confirmed the fix on the only hardware that exhibits it — none of my units report a non-zero differential, so this is one I could not have caught or verified alone. His confirmation covers COOL; the HEAT path follows the same rule but has not been measured.

Update through HACS and restart; nothing to reconfigure. **ESPHome component users need to rebuild** with `ref: v3.9.2` — it reads and applies the same differential now.

Release notes: https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.2

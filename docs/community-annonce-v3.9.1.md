# community.home-assistant.io announcement — v3.9.1

> Post in: https://community.home-assistant.io/t/control-daikin-madoka-brc1h-thermostat-via-bluetooth-ha-custom-integration-esphome-component/984675
> As a reply to the single-setpoint report (post #23 / #24).
> Do not paste this header — the post starts after the rule below.

---

Back — and this one is fixed. **v3.9.1** is out.

@mauriziofanetti-hue, your analysis was correct down to the line, and the payload dump is what made this a five-minute fix instead of a week of guessing. Writing `cooling = 22` alongside an unchanged `heating = 24` produces a pair that a unit configured for single-setpoint logic never holds — and it rejects the frame in silence, exactly as it did with the zeroed limits in v2.4.0, one field over. AUTO was unaffected because both branches fired there, which is why it only ever showed in COOL and HEAT.

Both setpoints are now written to the target whenever range mode is off. Verified here on hardware before tagging — the outgoing frame now reads `20 02 0d80 / 21 02 0d80`, the same value in both registers.

@aureliofrohlich, your confirmation mattered too: the same behaviour through an ESPHome proxy *and* through a host adapter told me it was not adapter-specific, which ruled out a whole branch of investigation.

The same release also fixes a setup crash for anyone running an integration whose devices use more than two identifier elements (`rfxtrx`, for one) — reported by @speynaud on GitHub.

Update through HACS and restart; nothing to reconfigure. **If you use the ESPHome component rather than the integration, it had the same bug and you need to rebuild** with `ref: v3.9.1`.

Release notes: https://github.com/dasimon135/daikin_madoka/releases/tag/v3.9.1

/*
 * Madoka Card — a dial-style Lovelace card for the Daikin BRC1H thermostat.
 * Ships with the daikin_madoka integration (auto-registered, no separate install).
 * Vanilla custom element: no external dependencies, works across HA versions.
 */
const MADOKA_CARD_VERSION = "0.6.0";
const SETPOINT_MODES = ["cool", "heat", "auto"]; // modes where a target is meaningful

const MODES = {
  cool: { label: "Cooling", color: "#38c6ff", color2: "#4d8bff", mdi: "mdi:snowflake" },
  heat: { label: "Heating", color: "#ff7a3d", color2: "#ff5152", mdi: "mdi:fire" },
  auto: { label: "Auto", color: "#8a5cff", color2: "#5b74ff", mdi: "mdi:autorenew" },
  fan_only: { label: "Fan", color: "#35e0b0", color2: "#2bc6c6", mdi: "mdi:fan" },
  dry: { label: "Dry", color: "#ffc93d", color2: "#ff9f3d", mdi: "mdi:water-percent" },
  off: { label: "Off", color: "#565a6e", color2: "#3d4050", mdi: "mdi:power" },
};
const MODE_ORDER = ["cool", "heat", "auto", "fan_only", "dry", "off"];

// Card-specific words. Mode names come from HA's own climate translations
// (hass.localize) so they always match the user's HA language; these cover
// only the labels HA does not provide. English is the fallback.
const CARD_STRINGS = {
  en: { to: "to", standby: "standby", display: "Display", fan: "Fan", fanMode: "Fan", filterOk: "OK", filterClean: "Clean" },
  fr: { to: "vers", standby: "en veille", display: "Voyant", fan: "Ventilation", fanMode: "Ventilation", filterOk: "OK", filterClean: "Nettoyer" },
  es: { to: "a", standby: "en espera", display: "Pantalla", fan: "Ventilación", fanMode: "Ventilación", filterOk: "OK", filterClean: "Limpiar" },
  de: { to: "auf", standby: "Standby", display: "Anzeige", fan: "Lüftung", fanMode: "Lüftung", filterOk: "OK", filterClean: "Reinigen" },
  it: { to: "a", standby: "in pausa", display: "Display", fan: "Ventola", fanMode: "Ventola", filterOk: "OK", filterClean: "Pulire" },
  nl: { to: "naar", standby: "stand-by", display: "Display", fan: "Ventilatie", fanMode: "Ventilatie", filterOk: "OK", filterClean: "Reinigen" },
};
const FAN_SHORT = {
  en: { auto: "Auto", low: "Low", medium: "Mid", high: "High" },
  fr: { auto: "Auto", low: "Bas", medium: "Moy", high: "Haut" },
  es: { auto: "Auto", low: "Baja", medium: "Med", high: "Alta" },
  de: { auto: "Auto", low: "Nied", medium: "Mit", high: "Hoch" },
  it: { auto: "Auto", low: "Bas", medium: "Med", high: "Alt" },
  nl: { auto: "Auto", low: "Laag", medium: "Mid", high: "Hoog" },
};
const ARC_LEN = 207; // visible arc length (270° of the r=44 ring)
const MIN_FALLBACK = 16, MAX_FALLBACK = 32;

const svg = (inner, cls) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"${cls ? ` class="${cls}"` : ""}>${inner}</svg>`;
// Native HA / Material Design icons — crisp and familiar.
const mdi = (name, cls) => `<ha-icon icon="${name}"${cls ? ` class="${cls}"` : ""}></ha-icon>`;

class MadokaCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._histAt = 0;
    this._histEntity = null;
    this._histPoints = null;
    this._dragY = null;
  }

  static getStubConfig(hass) {
    const climate = Object.keys(hass.states).find((e) => e.startsWith("climate."));
    return { entity: climate || "climate.madoka" };
  }

  setConfig(config) {
    if (!config || !config.entity || !config.entity.startsWith("climate.")) {
      throw new Error("Set an 'entity' pointing at a climate.* entity");
    }
    this._config = config;
    if (this._built) this._update();
  }

  getCardSize() { return this._config && this._layout() === "tile" ? 1 : 7; }

  /* ------------------------------ i18n ------------------------------ */
  _lang() { return ((this._hass && this._hass.language) || "en").split("-")[0]; }
  _t(key) {
    const l = this._lang();
    return (CARD_STRINGS[l] && CARD_STRINGS[l][key]) || CARD_STRINGS.en[key];
  }
  _modeLabel(mode) {
    // HA translates fan_only as the verbose "Fan only" / "Ventilation
    // uniquement"; use a short card-specific label for this one mode.
    if (mode === "fan_only") return this._t("fanMode");
    const t = this._hass.localize &&
      this._hass.localize(`component.climate.entity_component._.state.${mode}`);
    return t || (MODES[mode] ? MODES[mode].label : mode);
  }
  _fanLabel(mode) {
    const k = String(mode).toLowerCase(), l = this._lang();
    if (FAN_SHORT[l] && FAN_SHORT[l][k]) return FAN_SHORT[l][k];
    if (FAN_SHORT.en[k]) return FAN_SHORT.en[k];
    if (this._hass.formatEntityAttributeValue) {
      try { return this._hass.formatEntityAttributeValue(this._hass.states[this._config.entity], "fan_mode", mode); } catch (e) { /* noop */ }
    }
    return mode;
  }
  _unavailLabel(st) {
    if (this._hass.formatEntityState) {
      try { return this._hass.formatEntityState(st); } catch (e) { /* noop */ }
    }
    return "Unavailable";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  /* ---------- entity resolution (zero-config sibling discovery) ---------- */
  _resolve() {
    const cfg = this._config, hass = this._hass;
    const out = {
      outdoor: cfg.outdoor_entity || null,
      indoor: cfg.temperature_entity || null,
      brightness: cfg.brightness_entity || null,
      filter: cfg.filter_entity || null,
      rssi: cfg.rssi_entity || null,
    };
    const reg = hass.entities || {};
    const devId = reg[cfg.entity] && reg[cfg.entity].device_id;
    if (devId) {
      for (const eid of Object.keys(reg)) {
        if (reg[eid].device_id !== devId || eid === cfg.entity) continue;
        const st = hass.states[eid];
        if (!st) continue;
        const domain = eid.split(".")[0];
        const dc = st.attributes.device_class;
        if (!out.outdoor && domain === "sensor" && /outdoor|exterieur|exterior/.test(eid)) out.outdoor = eid;
        if (!out.indoor && domain === "sensor" && dc === "temperature" && /indoor|interieur|interior/.test(eid)) out.indoor = eid;
        if (!out.brightness && domain === "number") out.brightness = eid;
        if (!out.filter && domain === "binary_sensor" && dc === "problem") out.filter = eid;
        if (!out.rssi && domain === "sensor" && dc === "signal_strength") out.rssi = eid;
      }
    }
    return out;
  }

  /* ---------------------------- rendering ---------------------------- */
  _update() {
    if (!this._hass || !this._config) return;
    const st = this._hass.states[this._config.entity];
    const root = this.shadowRoot;
    if (!st) {
      root.getElementById("err").textContent = `Entity ${this._config.entity} not found`;
      root.getElementById("err").style.display = "block";
      root.getElementById("card").style.display = "none";
      return;
    }
    root.getElementById("err").style.display = "none";
    const cardEl = root.getElementById("card");
    cardEl.style.display = "flex";
    if (this._layout() === "tile") { this._updateTile(st); return; }
    cardEl.classList.toggle("compact", this._layout() === "compact");

    const a = st.attributes;
    const ids = this._resolve();
    const hvac = st.state; // off / cool / heat / auto / dry / fan_only
    const unavailable = hvac === "unavailable" || hvac === "unknown";
    const on = !unavailable && hvac !== "off";
    const modeKey = MODES[hvac] ? hvac : "off";
    const M = MODES[modeKey];
    const min = a.min_temp != null ? a.min_temp : MIN_FALLBACK;
    const max = a.max_temp != null ? a.max_temp : MAX_FALLBACK;
    const isRange = a.target_temp_low != null && a.target_temp_high != null;

    // state color
    root.host.style.setProperty("--state", M.color);
    root.host.style.setProperty("--state-2", M.color2);
    root.getElementById("dial").classList.toggle("off", !on);

    // title
    root.getElementById("title").textContent = this._config.name || a.friendly_name || "Madoka";

    // center: mode + ambient + target
    root.getElementById("modeRow").innerHTML = on
      ? mdi(M.mdi) + `<span>${this._modeLabel(hvac)}</span>`
      : `<span>${unavailable ? this._unavailLabel(st) : this._modeLabel("off")}</span>`;
    const cur = a.current_temperature;
    root.getElementById("ambient").textContent = cur != null ? Math.round(cur) : "--";

    const tb = root.getElementById("targetBox");
    const meaningful = SETPOINT_MODES.includes(hvac);
    if (!on) {
      tb.className = "target";
      tb.innerHTML = unavailable ? "" : `<span>${this._t("standby")}</span>`;
      this._setArc(null, min, max);
    } else if (!meaningful) {
      // Fan / Dry: no relevant setpoint — keep the readout clean.
      tb.className = "target"; tb.innerHTML = "";
      this._setArc(null, min, max);
    } else if (isRange) {
      tb.className = "target range";
      tb.innerHTML = `<span class="goal low">${Math.round(a.target_temp_low)}°</span><span>–</span>` +
        `<span class="goal high">${Math.round(a.target_temp_high)}°</span>`;
      this._setArc(a.target_temp_high, min, max);
    } else {
      tb.className = "target";
      const t = a.temperature;
      tb.innerHTML = `<span>${this._t("to")}</span><span class="goal">${t != null ? Math.round(t) : "--"}°</span>`;
      this._setArc(t, min, max);
    }

    // fan segments + selector
    this._renderFan(on, a);

    // mode switcher (only modes the device supports + off)
    const supported = (a.hvac_modes || MODE_ORDER).filter((m) => MODES[m]);
    const order = MODE_ORDER.filter((m) => supported.includes(m));
    root.getElementById("modes").innerHTML = order.map((m) =>
      `<button class="mode-btn" role="tab" data-mode="${m}" aria-selected="${m === hvac}">` +
      mdi(MODES[m].mdi) + `<span>${this._modeLabel(m)}</span></button>`).join("");

    // localized static labels
    root.getElementById("fanLbl").textContent = this._t("fan");
    root.getElementById("brightLbl").textContent = this._t("display");

    // chips: rssi, outdoor, filter
    this._renderChips(ids);

    // brightness slider
    this._renderBrightness(ids);

    // graph
    this._maybeGraph(ids, min, max);

    // aria
    root.getElementById("dial").setAttribute("aria-valuenow", a.temperature != null ? a.temperature : "");
    this._min = min; this._max = max; this._isRange = isRange; this._on = on;
  }

  _setArc(temp, min, max) {
    const el = this.shadowRoot.getElementById("arcFill");
    if (temp == null) { el.style.opacity = "0.25"; return; }
    el.style.opacity = "1";
    const frac = Math.min(1, Math.max(0, (temp - min) / (max - min)));
    el.setAttribute("stroke-dashoffset", (ARC_LEN * (1 - frac)).toFixed(1));
  }

  _renderFan(on, a) {
    const modes = a.fan_modes || [];
    const cur = a.fan_mode;
    const key = (cur || "").toLowerCase();
    // 3 bars = the 3 real speeds. High fills all three; Auto fills all three
    // but pulses (it is adaptive, not "faster than High").
    const rank = { low: 1, medium: 2, high: 3, auto: 3 };
    const level = on ? (rank[key] || 3) : 0;
    const fanEl = this.shadowRoot.getElementById("fan");
    fanEl.classList.toggle("auto", on && key === "auto");
    this.shadowRoot.querySelectorAll("#fan i").forEach((el, i) =>
      el.classList.toggle("on", i < level));
    fanEl.style.opacity = on ? "1" : "0.35";
    const sel = this.shadowRoot.getElementById("fanSel");
    if (!modes.length) { sel.style.display = "none"; return; }
    sel.style.display = "flex";
    sel.innerHTML = modes.map((m) =>
      `<button class="fanbtn" data-fan="${m}" aria-pressed="${m === cur}">${this._fanLabel(m)}</button>`
    ).join("");
  }

  _renderChips(ids) {
    const hass = this._hass;
    const wrap = this.shadowRoot.getElementById("chips");
    const chips = [];
    if (ids.rssi && hass.states[ids.rssi]) {
      const v = hass.states[ids.rssi].state;
      chips.push(`<span class="chip" title="Bluetooth signal">` +
        mdi("mdi:bluetooth") + `${v}</span>`);
    }
    if (ids.outdoor && hass.states[ids.outdoor]) {
      const v = hass.states[ids.outdoor].state;
      if (v != null && v !== "unknown" && v !== "unavailable") {
        chips.push(`<span class="chip" title="Outdoor">` +
          mdi("mdi:thermometer") + `${Math.round(Number(v))}°</span>`);
      }
    }
    if (ids.filter && hass.states[ids.filter]) {
      const on = hass.states[ids.filter].state === "on";
      chips.push(`<span class="chip ${on ? "warn" : ""}" title="Filter">` +
        mdi("mdi:air-filter") + `${on ? this._t("filterClean") : this._t("filterOk")}</span>`);
    }
    wrap.innerHTML = chips.join("");
  }

  _renderBrightness(ids) {
    const row = this.shadowRoot.getElementById("brightRow");
    if (!ids.brightness || !this._hass.states[ids.brightness]) { row.style.display = "none"; return; }
    row.style.display = "flex";
    const st = this._hass.states[ids.brightness];
    const min = st.attributes.min != null ? st.attributes.min : 0;
    const max = st.attributes.max != null ? st.attributes.max : 19;
    const val = Number(st.state);
    const sl = this.shadowRoot.getElementById("bright");
    sl.min = min; sl.max = max; sl.value = isNaN(val) ? min : val;
    this._brightEntity = ids.brightness;
  }

  /* ------------------------------ graph ------------------------------ */
  _maybeGraph(ids, min, max) {
    const entity = ids.indoor || this._config.entity;
    const now = Date.now();
    if (entity === this._histEntity && now - this._histAt < 5 * 60 * 1000) {
      this._drawGraph(min, max);
      return;
    }
    this._histEntity = entity;
    this._histAt = now;
    const start = new Date(now - 12 * 3600 * 1000).toISOString();
    const useAttr = !ids.indoor; // climate: current_temperature lives in attributes
    this._hass.callWS({
      type: "history/history_during_period",
      start_time: start,
      entity_ids: [entity],
      minimal_response: !useAttr,
      no_attributes: !useAttr,
      significant_changes_only: false,
    }).then((res) => {
      const rows = res && res[entity] ? res[entity] : [];
      const pts = [];
      for (const r of rows) {
        const v = useAttr
          ? (r.a && r.a.current_temperature)
          : Number(r.s);
        if (v == null || isNaN(v)) continue;
        pts.push(v);
      }
      this._histPoints = pts.slice(-120);
      this._drawGraph(min, max);
    }).catch(() => { this._histPoints = null; this._drawGraph(min, max); });
  }

  _drawGraph(min, max) {
    const el = this.shadowRoot.getElementById("spark");
    const pts = this._histPoints;
    if (!pts || pts.length < 2) { el.innerHTML = ""; return; }
    const W = 100, H = 28, pad = 2;
    const lo = Math.min(...pts), hi = Math.max(...pts);
    const span = hi - lo || 1;
    const stepX = (W - pad * 2) / (pts.length - 1);
    const y = (v) => H - pad - ((v - lo) / span) * (H - pad * 2);
    let d = "";
    pts.forEach((v, i) => { d += (i ? "L" : "M") + (pad + i * stepX).toFixed(1) + " " + y(v).toFixed(1) + " "; });
    const area = d + `L${(pad + (pts.length - 1) * stepX).toFixed(1)} ${H} L${pad} ${H} Z`;
    el.innerHTML =
      `<path class="area" d="${area}"/><path class="line" d="${d}"/>` +
      `<circle class="dot" cx="${(pad + (pts.length - 1) * stepX).toFixed(1)}" cy="${y(pts[pts.length - 1]).toFixed(1)}" r="1.6"/>`;
  }

  /* ---------------------------- services ---------------------------- */
  _call(domain, service, data) {
    this._hass.callService(domain, service, Object.assign({ entity_id: this._config.entity }, data));
  }
  _bump(delta) {
    if (!this._on) return;
    const st = this._hass.states[this._config.entity].attributes;
    if (this._isRange) {
      const hi = Math.min(this._max, Math.round(st.target_temp_high) + delta);
      this._call("climate", "set_temperature", { target_temp_low: st.target_temp_low, target_temp_high: hi });
    } else {
      const t = Math.min(this._max, Math.max(this._min, Math.round(st.temperature) + delta));
      this._call("climate", "set_temperature", { temperature: t });
    }
  }
  _setMode(m) { this._call("climate", "set_hvac_mode", { hvac_mode: m }); }
  _setFan(m) { this._call("climate", "set_fan_mode", { fan_mode: m }); }
  _power() {
    const cur = this._hass.states[this._config.entity].state;
    if (cur === "off") {
      const modes = this._hass.states[this._config.entity].attributes.hvac_modes || ["cool"];
      const restore = ["cool", "heat", "auto"].find((m) => modes.includes(m)) || modes.find((m) => m !== "off");
      this._setMode(restore || "cool");
    } else {
      this._setMode("off");
    }
  }

  /* ---------------------------- build DOM ---------------------------- */
  _layout() {
    return this._config.layout || (this._config.compact ? "compact" : "full");
  }

  _buildTile() {
    this.shadowRoot.innerHTML = this._tileTemplate();
    const root = this.shadowRoot;
    root.getElementById("tdot").addEventListener("click", () => this._power());
    root.getElementById("tminus").addEventListener("click", () => this._bump(-1));
    root.getElementById("tplus").addEventListener("click", () => this._bump(1));
    // Tapping the name/state opens HA's more-info dialog, like native tiles.
    const info = root.getElementById("tinfo");
    info.addEventListener("click", () => this._moreInfo());
    info.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this._moreInfo(); }
    });
  }

  _moreInfo() {
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId: this._config.entity },
      bubbles: true, composed: true,
    }));
  }

  _build() {
    this._built = true;
    if (this._layout() === "tile") { this._buildTile(); return; }
    this.shadowRoot.innerHTML = this._template();
    const root = this.shadowRoot;
    root.getElementById("plus").addEventListener("click", () => this._bump(1));
    root.getElementById("minus").addEventListener("click", () => this._bump(-1));
    root.getElementById("power").addEventListener("click", () => this._power());
    root.getElementById("modes").addEventListener("click", (e) => {
      const b = e.target.closest(".mode-btn"); if (b) this._setMode(b.dataset.mode);
    });
    root.getElementById("fanSel").addEventListener("click", (e) => {
      const b = e.target.closest(".fanbtn"); if (b) this._setFan(b.dataset.fan);
    });
    const br = root.getElementById("bright");
    br.addEventListener("change", () => {
      if (this._brightEntity) this._hass.callService("number", "set_value",
        { entity_id: this._brightEntity, value: Number(br.value) });
    });
    const dial = root.getElementById("dial");
    dial.addEventListener("keydown", (e) => {
      if (["ArrowUp", "ArrowRight"].includes(e.key)) { this._bump(1); e.preventDefault(); }
      if (["ArrowDown", "ArrowLeft"].includes(e.key)) { this._bump(-1); e.preventDefault(); }
    });
    dial.addEventListener("pointerdown", (e) => { this._dragY = e.clientY; dial.setPointerCapture(e.pointerId); });
    dial.addEventListener("pointermove", (e) => {
      if (this._dragY == null) return;
      const dy = this._dragY - e.clientY;
      if (Math.abs(dy) >= 16) { this._bump(dy > 0 ? 1 : -1); this._dragY = e.clientY; }
    });
    const end = () => { this._dragY = null; };
    dial.addEventListener("pointerup", end);
    dial.addEventListener("pointercancel", end);
  }

  _template() {
    return `<style>${this._css()}</style>
<div id="err" class="err"></div>
<div class="card" id="card">
  <div class="head">
    <b id="title">Madoka</b>
    <div class="chips" id="chips"></div>
  </div>
  <div class="dial-wrap">
    <div class="dial" id="dial" role="slider" aria-label="Target temperature"
         aria-valuemin="16" aria-valuemax="32" tabindex="0">
      <div class="halo"></div>
      <svg class="arc" viewBox="0 0 100 100" aria-hidden="true">
        <circle class="track" cx="50" cy="50" r="44" stroke-dasharray="207 276"/>
        <circle class="fill" id="arcFill" cx="50" cy="50" r="44" stroke-dasharray="207 276" stroke-dashoffset="0"/>
      </svg>
      <div class="readout">
        <div class="mode-row" id="modeRow"></div>
        <div class="temp"><span id="ambient">--</span><span class="deg">°</span></div>
        <div class="target" id="targetBox"></div>
        <div class="fan" id="fan"><i></i><i></i><i></i></div>
      </div>
    </div>
  </div>
  <div class="controls">
    <button class="ctl" id="minus" type="button" aria-label="Lower">−</button>
    <button class="ctl power" id="power" type="button" aria-label="Power">
      ${mdi("mdi:power")}
    </button>
    <button class="ctl" id="plus" type="button" aria-label="Raise">+</button>
  </div>
  <div class="fanrow"><span class="lbl" id="fanLbl">Fan</span><div class="fansel" id="fanSel"></div></div>
  <div class="brightrow" id="brightRow">
    <span class="lbl" id="brightLbl">Display</span>
    <input type="range" id="bright" min="0" max="19" step="1"/>
    ${mdi("mdi:brightness-6", "brighticon")}
  </div>
  <svg class="graph" id="spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"></svg>
  <div class="modes" id="modes" role="tablist"></div>
</div>`;
  }

  _tileTemplate() {
    return `<style>${this._css()}</style>
<div id="err" class="err"></div>
<div class="card tile" id="card">
  <button class="tdot" id="tdot" type="button" aria-label="Power"><ha-icon id="ticon"></ha-icon></button>
  <div class="tinfo" id="tinfo" role="button" tabindex="0" aria-label="Details">
    <span class="tname" id="tname">Madoka</span>
    <span class="tsub" id="tsub"></span>
  </div>
  <div class="tctl">
    <button class="tbtn" id="tminus" type="button" aria-label="Lower">−</button>
    <button class="tbtn" id="tplus" type="button" aria-label="Raise">+</button>
  </div>
</div>`;
  }

  _updateTile(st) {
    const root = this.shadowRoot;
    const a = st.attributes;
    const hvac = st.state;
    const unavailable = hvac === "unavailable" || hvac === "unknown";
    const on = !unavailable && hvac !== "off";
    const M = MODES[MODES[hvac] ? hvac : "off"];
    root.host.style.setProperty("--state", M.color);
    root.host.style.setProperty("--state-2", M.color2);
    root.getElementById("card").classList.toggle("off", !on);
    root.getElementById("ticon").setAttribute("icon", unavailable ? "mdi:bluetooth-off" : M.mdi);
    root.getElementById("tname").textContent =
      this._config.name || a.friendly_name || "Madoka";

    const cur = a.current_temperature != null ? Math.round(a.current_temperature) : "--";
    const meaningful = SETPOINT_MODES.includes(hvac);
    let sub;
    if (unavailable) {
      sub = this._unavailLabel(st);
    } else if (!on) {
      // Keep the ambient temperature visible even when the unit is off.
      sub = a.current_temperature != null
        ? `${cur}° · ${this._modeLabel("off")}`
        : this._modeLabel("off");
    } else if (a.target_temp_low != null && a.target_temp_high != null) {
      sub = `${cur}° → ${Math.round(a.target_temp_low)}–${Math.round(a.target_temp_high)}° · ${this._modeLabel(hvac)}`;
    } else if (meaningful && a.temperature != null) {
      sub = `${cur}° → ${Math.round(a.temperature)}° · ${this._modeLabel(hvac)}`;
    } else {
      sub = `${cur}° · ${this._modeLabel(hvac)}`;
    }
    root.getElementById("tsub").textContent = sub;

    const disabled = !on || !meaningful;
    root.getElementById("tminus").disabled = disabled;
    root.getElementById("tplus").disabled = disabled;

    // state used by _bump / _power
    this._min = a.min_temp != null ? a.min_temp : MIN_FALLBACK;
    this._max = a.max_temp != null ? a.max_temp : MAX_FALLBACK;
    this._isRange = a.target_temp_low != null && a.target_temp_high != null;
    this._on = on;
  }

  _css() {
    return `
:host {
  --face: #16161d; --face-2: #1d1d27; --bezel: #0a0a0e; --face-edge: #34364a;
  --screen-ink: #cabfff; --dev-ink: #f2f0ff; --dev-soft: #8f8ca8; --dev-hairline: #2c2c3a;
  --state: #8a5cff; --state-2: #5b74ff;
  --panel: var(--ha-card-background, var(--card-background-color, #fff));
  --ink: var(--primary-text-color, #1b1a26);
  --ink-soft: var(--secondary-text-color, #5b5a6e);
  --hairline: var(--divider-color, #dcdae6);
  --accent: var(--primary-color, #6d4bff);
  display: block;
}
* { box-sizing: border-box; }
.err { display:none; padding:16px; color:#c0392b; font:14px system-ui; }
.card {
  background: var(--panel); border-radius: var(--ha-card-border-radius, 16px);
  border: 1px solid var(--hairline); padding: 18px 18px 16px;
  display: flex; flex-direction: column; gap: 15px;
  font-family: var(--paper-font-body1_-_font-family, "Segoe UI", system-ui, sans-serif);
  color: var(--ink);
}
.head { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.head b { font-size:1rem; font-weight:600; letter-spacing:-.01em; }
.chips { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
.chip { display:inline-flex; align-items:center; gap:5px; font-size:.68rem; font-weight:600;
  color: var(--ink-soft); background: color-mix(in srgb, var(--accent) 8%, transparent);
  border:1px solid var(--hairline); padding:3px 8px; border-radius:999px; white-space:nowrap; }
.chip.warn { color:#d98324; background: color-mix(in srgb,#f0a33a 18%,transparent); border-color:transparent; }
.chip svg, .chip ha-icon { width:12px; height:12px; --mdc-icon-size:12px; }
.dial-wrap { display:grid; place-items:center; padding:4px 0 0; }
.dial { position:relative; width:250px; height:250px; border-radius:50%;
  background: radial-gradient(circle at 50% 38%, var(--face-2), var(--face) 62%, var(--bezel) 100%);
  box-shadow: inset 0 0 0 1px var(--face-edge), inset 0 2px 10px rgba(0,0,0,.6), 0 16px 36px -18px rgba(0,0,0,.8);
  display:grid; place-items:center; cursor:grab; touch-action:none; user-select:none; }
.dial:active { cursor:grabbing; }
.dial:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.halo { position:absolute; inset:15px; border-radius:50%; border:3px solid var(--state);
  box-shadow: 0 0 20px 2px color-mix(in srgb,var(--state) 70%,transparent),
    0 0 42px 6px color-mix(in srgb,var(--state) 42%,transparent),
    inset 0 0 16px 0 color-mix(in srgb,var(--state) 42%,transparent);
  transition: border-color .7s ease, box-shadow .7s ease, opacity .4s ease;
  animation: breathe 4.2s ease-in-out infinite; }
@keyframes breathe { 0%,100%{opacity:.82;transform:scale(1);} 50%{opacity:1;transform:scale(1.012);} }
.dial.off .halo { animation:none; opacity:.45; }
.arc { position:absolute; inset:8px; transform:rotate(135deg); pointer-events:none; }
.arc circle { fill:none; stroke-width:5; stroke-linecap:round; }
.arc .track { stroke: var(--dev-hairline); }
.arc .fill { stroke: var(--state); transition: stroke-dashoffset .5s cubic-bezier(.4,1.2,.4,1), stroke .7s ease, opacity .4s;
  filter: drop-shadow(0 0 4px color-mix(in srgb,var(--state) 60%,transparent)); }
.readout { position:relative; z-index:2; text-align:center; color:var(--dev-ink); line-height:1; }
.mode-row { display:flex; align-items:center; justify-content:center; gap:6px; height:18px; margin-bottom:6px; }
.mode-row svg, .mode-row ha-icon { width:16px; height:16px; color:var(--state); --mdc-icon-size:16px; }
.mode-row span { font-size:.62rem; letter-spacing:.14em; text-transform:uppercase; color:var(--dev-soft); font-weight:700; }
.temp { font-size:3.3rem; font-weight:350; letter-spacing:-.02em; font-variant-numeric:tabular-nums; display:inline-flex; align-items:flex-start; }
.temp .deg { font-size:1.3rem; font-weight:400; margin-top:.45rem; color:var(--dev-soft); }
.target { margin-top:5px; font-size:.8rem; color:var(--screen-ink); font-variant-numeric:tabular-nums; display:inline-flex; gap:6px; align-items:center; min-height:1.1em; }
.target .goal { color:var(--dev-ink); font-weight:650; }
.target.range .goal.low { color:#7fd0ff; } .target.range .goal.high { color:#ff9f7a; }
.fan { margin-top:10px; display:inline-flex; gap:4px; align-items:flex-end; height:16px; }
.fan i { width:4px; border-radius:2px; background:var(--dev-hairline); transition:background .3s,height .3s; }
.fan i:nth-child(1){height:7px;} .fan i:nth-child(2){height:11px;} .fan i:nth-child(3){height:15px;}
.fan i.on { background:var(--state); }
.fan.auto i.on { animation: fanpulse 1.6s ease-in-out infinite; }
@keyframes fanpulse { 0%,100%{opacity:.5;} 50%{opacity:1;} }
.controls { display:flex; align-items:center; justify-content:center; gap:28px; }
.ctl { width:44px; height:44px; border-radius:50%; border:1px solid var(--hairline);
  background: color-mix(in srgb,var(--accent) 6%,var(--panel)); color:var(--ink); font-size:1.35rem;
  display:grid; place-items:center; cursor:pointer; transition:transform .12s,background .2s,border-color .2s; }
.ctl:hover { border-color:var(--accent); background: color-mix(in srgb,var(--accent) 14%,var(--panel)); }
.ctl:active { transform:scale(.9); }
.ctl:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.ctl.power svg, .ctl.power ha-icon { width:20px; height:20px; --mdc-icon-size:20px; }
.fanrow, .brightrow { display:flex; align-items:center; gap:10px; }
.lbl { font-size:.68rem; text-transform:uppercase; letter-spacing:.12em; font-weight:700; color:var(--ink-soft); min-width:52px; }
.fansel { display:flex; gap:4px; flex:1; }
.fanbtn { flex:1; font-size:.72rem; font-weight:600; color:var(--ink-soft); background:transparent;
  border:1px solid var(--hairline); border-radius:8px; padding:5px 0; cursor:pointer; transition:all .16s; }
.fanbtn:hover { border-color:var(--accent); color:var(--ink); }
.fanbtn[aria-pressed="true"] { color:#fff; border-color:transparent; background:linear-gradient(135deg,var(--state),var(--state-2)); }
.brightrow input[type=range] { flex:1; accent-color: var(--state); height:4px; }
.brighticon { width:15px; height:15px; --mdc-icon-size:15px; color:var(--ink-soft); }
.graph { width:100%; height:34px; display:block; }
.graph .area { fill: color-mix(in srgb,var(--state) 16%,transparent); stroke:none; }
.graph .line { fill:none; stroke:var(--state); stroke-width:1.4; stroke-linejoin:round; stroke-linecap:round; }
.graph .dot { fill:var(--state); }
.modes { display:flex; flex-wrap:wrap; gap:6px; justify-content:center; }
.mode-btn { display:inline-flex; align-items:center; gap:6px; font-size:.74rem; font-weight:600; color:var(--ink-soft);
  background:transparent; border:1px solid var(--hairline); border-radius:999px; padding:6px 12px; cursor:pointer; transition:all .18s; }
.mode-btn svg, .mode-btn ha-icon { width:14px; height:14px; --mdc-icon-size:14px; }
.mode-btn:hover { border-color:var(--accent); color:var(--ink); }
.mode-btn[aria-selected="true"] { color:#fff; border-color:transparent;
  background:linear-gradient(135deg,var(--state),var(--state-2)); box-shadow:0 6px 16px -6px color-mix(in srgb,var(--state) 80%,transparent); }
.mode-btn:focus-visible, .fanbtn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
/* Tile (ultra-compact) layout — config: layout: tile */
.card.tile { flex-direction:row; align-items:center; gap:12px; padding:10px 12px; }
.tdot { flex:0 0 auto; width:42px; height:42px; border-radius:50%; border:none; cursor:pointer;
  display:grid; place-items:center;
  background: radial-gradient(circle at 50% 40%, color-mix(in srgb,var(--state) 45%, var(--face)), var(--face) 78%);
  box-shadow: 0 0 0 2px color-mix(in srgb,var(--state) 70%,transparent),
    0 0 14px 1px color-mix(in srgb,var(--state) 55%,transparent);
  transition: box-shadow .5s ease, background .5s ease; }
.tdot ha-icon { --mdc-icon-size:20px; width:20px; height:20px; color:var(--state); }
.card.tile.off .tdot { box-shadow: inset 0 0 0 1px var(--hairline); background:var(--face); }
.card.tile.off .tdot ha-icon { color:var(--ink-soft); }
.tinfo { flex:1 1 auto; min-width:0; display:flex; flex-direction:column; gap:1px; cursor:pointer; border-radius:8px; outline:none; }
.tinfo:focus-visible { box-shadow: 0 0 0 2px var(--accent); }
.tname { font-size:.92rem; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tsub { font-size:.76rem; color:var(--ink-soft); font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tctl { flex:0 0 auto; display:flex; gap:6px; }
.tbtn { width:34px; height:34px; border-radius:9px; border:1px solid var(--hairline);
  background: color-mix(in srgb,var(--accent) 6%,var(--panel)); color:var(--ink); font-size:1.1rem;
  cursor:pointer; transition:transform .12s,background .2s,border-color .2s; }
.tbtn:hover:not(:disabled) { border-color:var(--accent); background: color-mix(in srgb,var(--accent) 14%,var(--panel)); }
.tbtn:active:not(:disabled) { transform:scale(.9); }
.tbtn:disabled { opacity:.4; cursor:default; }
.tdot:focus-visible, .tbtn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

/* Compact variant (config: compact: true) — dial + controls + modes only */
.card.compact { gap:12px; padding:14px 14px 12px; }
.card.compact .dial { width:184px; height:184px; }
.card.compact .halo { inset:12px; }
.card.compact .temp { font-size:2.5rem; }
.card.compact .temp .deg { font-size:1rem; margin-top:.3rem; }
.card.compact .fan { margin-top:8px; }
.card.compact .controls { gap:22px; }
.card.compact .ctl { width:40px; height:40px; font-size:1.2rem; }
.card.compact .fanrow, .card.compact .brightrow, .card.compact .graph { display:none !important; }
@media (prefers-reduced-motion: reduce) { .halo, .fan.auto i.on { animation:none; } * { transition-duration:60ms !important; } }
`;
  }
}

if (!customElements.get("madoka-card")) {
  customElements.define("madoka-card", MadokaCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "madoka-card",
    name: "Madoka Card",
    preview: true,
    description: "Dial-style card for the Daikin BRC1H (Madoka) thermostat.",
  });
  console.info(`%c MADOKA-CARD %c ${MADOKA_CARD_VERSION} `,
    "color:#fff;background:#8a5cff;border-radius:3px 0 0 3px;padding:1px 4px",
    "color:#8a5cff;background:#2b2170;border-radius:0 3px 3px 0;padding:1px 4px");
}

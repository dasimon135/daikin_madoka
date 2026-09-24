// Loads the shipped card under a minimal DOM stub and runs named scenarios.
// Prints one JSON object {scenario: result}; tests/test_card_behaviour.py asserts on it.
const fs = require("fs");
const path = require("path");

const CARD = path.join(__dirname, "..", "custom_components", "daikin_madoka", "frontend", "madoka-card.js");

function makeElement() {
  const el = {
    style: { setProperty() {}, display: "" },
    classList: { toggle() {}, add() {}, remove() {} },
    dataset: {},
    attrs: {},
    children: [],
    disabled: false,
    hidden: false,
    innerHTML: "",
    textContent: "",
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {},
    appendChild(c) { this.children.push(c); },
    remove() {},
    setPointerCapture() {},
    querySelector() { return makeElement(); },
    querySelectorAll() { return []; },
  };
  return el;
}

function makeShadowRoot(host) {
  const byId = new Map();
  return {
    host,
    set innerHTML(html) {
      this._html = html;
      byId.clear();
      for (const m of html.matchAll(/id="([^"]+)"/g)) byId.set(m[1], makeElement());
    },
    get innerHTML() { return this._html; },
    // Like a browser: an id the current template does not have is null.
    getElementById(id) { return byId.get(id) || null; },
    querySelectorAll() { return []; },
    querySelector() { return makeElement(); },
  };
}

const registry = new Map();
global.HTMLElement = class {
  constructor() { this.style = { setProperty() {} }; }
  attachShadow() { this.shadowRoot = makeShadowRoot(this); return this.shadowRoot; }
  dispatchEvent() {}
};
global.customElements = { define: (n, c) => registry.set(n, c), get: (n) => registry.get(n) };
global.window = { customCards: [], addEventListener() {}, removeEventListener() {} };
global.document = {
  // The dialog host gets a shadow root of its own, like a real element, and a
  // registered tag name builds its class, like customElements does.
  createElement: (tag) => {
    const Registered = registry.get(String(tag).toLowerCase());
    if (Registered) return new Registered();
    const el = makeElement();
    el.attachShadow = () => {
      el.shadowRoot = makeShadowRoot(el);
      return el.shadowRoot;
    };
    return el;
  },
  body: makeElement(),
};
global.CustomEvent = class { constructor(t, i) { this.type = t; Object.assign(this, i); } };
const realSetTimeout = setTimeout;
// Keyed by id, so a cleared id never cancels a later timer.
const timers = new Map();
let timerId = 0;
global.setTimeout = (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; };
global.clearTimeout = (id) => { timers.delete(id); };
// Runs the queued timers whose delay is at most `upTo` ms; longer ones stay
// queued, so the 600 ms send can fire without the 15 s expiry. Shortest first,
// as a clock would.
const flushTimers = (upTo = Infinity) => {
  for (const [id, t] of [...timers].sort((x, y) => x[1].ms - y[1].ms)) {
    if (t.ms > upTo) continue;
    timers.delete(id);
    t.fn();
  }
};
// Lets settled promises (a rejected service call) run their handlers.
const settle = () => new Promise((r) => realSetTimeout(r, 0));
// A service call the card never catches would kill the whole run; count it.
const unhandled = [];
process.on("unhandledRejection", (e) => unhandled.push(String(e)));
console.info = () => {};

// eslint-disable-next-line no-eval
(0, eval)(fs.readFileSync(CARD, "utf-8"));
const MadokaCard = registry.get("madoka-card");

const ENTITY = "climate.salon";
function makeHass({ state = "cool", attrs = {}, entities = {}, states = {} } = {}) {
  const calls = [];
  return {
    calls,
    language: "en",
    localize: () => "",
    entities,
    states: Object.assign({
      [ENTITY]: {
        state,
        attributes: Object.assign({
          temperature: 25, current_temperature: 24, min_temp: 16, max_temp: 32,
          hvac_modes: ["off", "cool", "heat", "fan_only"], fan_modes: ["low", "high"], fan_mode: "low",
        }, attrs),
      },
    }, states),
    callService(domain, service, data) { calls.push({ domain, service, data }); return Promise.resolve(); },
    callWS() { return Promise.resolve({}); },
  };
}
function makeCard(config, hass) {
  const card = new MadokaCard();
  card.setConfig(Object.assign({ entity: ENTITY }, config));
  card.hass = hass;
  return card;
}
const setTemps = (hass) => hass.calls.filter((c) => c.service === "set_temperature").map((c) => c.data.temperature);

const scenarios = {
  // Three quick presses must end at +3, not at +1 three times.
  rapid_presses_accumulate() {
    const hass = makeHass();
    const card = makeCard({}, hass);
    card._bump(1); card._bump(1); card._bump(1);
    flushTimers();
    return setTemps(hass);
  },
  // Fan / dry have no setpoint worth moving; a null one must never become min_temp.
  no_setpoint_write_in_fan_mode() {
    const hass = makeHass({ state: "fan_only", attrs: { temperature: null } });
    const card = makeCard({}, hass);
    card._bump(1);
    flushTimers();
    return hass.calls.length;
  },
  // Editing `layout` on a live card must rebuild it, not render a tile into the dial.
  layout_switch_rebuilds() {
    const hass = makeHass();
    const card = makeCard({ layout: "full" }, hass);
    try {
      card.setConfig({ entity: ENTITY, layout: "tile" });
      return { threw: false, tile: card.shadowRoot.getElementById("tdot") !== null };
    } catch (e) {
      return { threw: true, message: String(e) };
    }
  },
  // A hass update that changes nothing this card shows must not re-render it.
  unrelated_state_change_is_skipped() {
    const hass = makeHass();
    const card = makeCard({}, hass);
    let updates = 0;
    const original = card._update.bind(card);
    card._update = () => { updates += 1; original(); };
    const next = Object.assign({}, hass, { states: Object.assign({}, hass.states, { "light.other": { state: "on", attributes: {} } }) });
    card.hass = next;
    return updates;
  },
  // Sibling discovery must not depend on the language of the entity_id.
  siblings_found_by_translation_key() {
    const entities = {
      [ENTITY]: { device_id: "dev1" },
      "sensor.salone_temperatura_esterna": { device_id: "dev1", translation_key: "outdoor_temperature" },
      "sensor.salone_temperatura_interna": { device_id: "dev1", translation_key: "indoor_temperature" },
    };
    const states = {
      "sensor.salone_temperatura_esterna": { state: "12", attributes: { device_class: "temperature" } },
      "sensor.salone_temperatura_interna": { state: "24", attributes: { device_class: "temperature" } },
    };
    const card = makeCard({}, makeHass({ entities, states }));
    const ids = card._resolve();
    return { outdoor: ids.outdoor, indoor: ids.indoor };
  },
  // The readout follows the presses at once; HA catches up seconds later.
  pending_target_is_shown() {
    const hass = makeHass();
    const card = makeCard({}, hass);
    card._bump(1); card._bump(1);
    return card.shadowRoot.getElementById("targetBox").innerHTML.includes("27");
  },
  // ...and stops standing in once the entity reports it.
  pending_target_clears_when_confirmed() {
    const hass = makeHass();
    const card = makeCard({}, hass);
    card._bump(1);
    flushTimers(1000);
    const st = hass.states[ENTITY];
    const next = Object.assign({}, hass, { states: Object.assign({}, hass.states, {
      [ENTITY]: { state: st.state, attributes: Object.assign({}, st.attributes, { temperature: 26 }) } }) });
    card.hass = next;
    return card._pending;
  },
  // The dial's +/- are dead weight where the tile already disables them.
  bump_buttons_disabled_without_a_setpoint() {
    const card = makeCard({}, makeHass({ state: "fan_only" }));
    return [card.shadowRoot.getElementById("plus").disabled, card.shadowRoot.getElementById("minus").disabled,
      card.shadowRoot.getElementById("power").disabled];
  },
  // speynaud, issue #100: reading "-3h" means doing mental arithmetic, and the
  // row already ended with an absolute time, so it mixed the two.
  graph_times_are_absolute_hours() {
    const hass = makeHass();
    const card = makeCard({ show_graph_times: true }, hass);
    const now = Date.UTC(2026, 8, 20, 14, 30) ;
    card._histPoints = [
      { t: now - 11 * 3600 * 1000, v: 20 },
      { t: now - 5 * 3600 * 1000, v: 22 },
      { t: now, v: 24 },
    ];
    card._drawGraph(16, 32);
    const html = card.shadowRoot.getElementById("sparkTimes").innerHTML;
    return { relative: /-\d+h/.test(html), labels: (html.match(/>([^<]+)</g) || []).map((m) => m.slice(1, -1)) };
  },
  // @speynaud on the Aidoo issue: the hour marks repeated the same minutes as
  // "now" four times over. Round hours can be labelled by the hour alone, and
  // only the newest reading needs its minutes.
  graph_times_are_round_hours() {
    const hass = makeHass();
    hass.language = "fr";
    const card = makeCard({ show_graph_times: true }, hass);
    const now = new Date("2026-09-20T18:37:00Z").getTime();
    card._histPoints = [
      { t: now - 11 * 3600 * 1000, v: 20 },
      { t: now - 5 * 3600 * 1000, v: 22 },
      { t: now, v: 24 },
    ];
    card._drawGraph(16, 32);
    const html = card.shadowRoot.getElementById("sparkTimes").innerHTML;
    const labels = (html.match(/>([^<]+)</g) || []).map((m) => m.slice(1, -1));
    return {
      labels,
      // Only the last one carries minutes, and they are the newest point's.
      withMinutes: labels.filter((l) => /[0-9][:h ][0-9][0-9]/.test(l)),
    };
  },
  // The popup is "this card, full size": dropping the options the user set on
  // the tile made show_graph_times, show_decimals and the entity overrides
  // vanish the moment it opened.
  popup_keeps_the_card_options() {
    const hass = makeHass();
    const card = makeCard({
      layout: "tile",
      show_graph_times: true,
      show_decimals: true,
      outdoor_entity: "sensor.outside",
      name: "Salon",
    }, hass);
    card._openCardDialog();
    const cfg = card._dialogCard._config;
    card._closeCardDialog();
    return {
      layout: cfg.layout,
      show_graph_times: cfg.show_graph_times,
      show_decimals: cfg.show_decimals,
      outdoor_entity: cfg.outdoor_entity,
      name: cfg.name,
    };
  },
  // @speynaud's own card on v3.13.5: history from about 09:00 to 19:29, and the
  // 19 h mark (29 minutes before the end) printed over "19:29". Both sit past
  // 88% of the width, where labels are right-aligned, so they overlapped.
  graph_marks_do_not_overprint_the_last_reading() {
    const hass = makeHass();
    hass.language = "fr";
    const card = makeCard({ show_graph_times: true }, hass);
    const end = new Date(2026, 8, 21, 19, 29).getTime();
    const start = new Date(2026, 8, 21, 9, 5).getTime();
    card._histPoints = [
      { t: start, v: 23 }, { t: start + 4 * 3600 * 1000, v: 24 }, { t: end, v: 23 },
    ];
    card._drawGraph(16, 32);
    const html = card.shadowRoot.getElementById("sparkTimes").innerHTML;
    const spans = [...html.matchAll(/left:([0-9.]+)%">([^<]+)</g)].map((m) => ({ left: Number(m[1]), label: m[2] }));
    return { labels: spans.map((s) => s.label), lefts: spans.map((s) => s.left) };
  },
  // Attribute values from any climate entity end up in innerHTML: they must be escaped.
  fan_mode_markup_is_escaped() {
    const hass = makeHass({ attrs: { fan_modes: ['<img src=x onerror=1>'], fan_mode: null } });
    const card = makeCard({}, hass);
    return card.shadowRoot.getElementById("fanSel").innerHTML.includes("<img");
  },
  // An hvac state the card does not know is printed as-is: it must be escaped too.
  mode_label_markup_is_escaped() {
    const hass = makeHass({ state: "<img src=x onerror=1>" });
    const card = makeCard({}, hass);
    return card.shadowRoot.getElementById("modeRow").innerHTML.includes("<img");
  },
  // Power on from off: a device that can turn itself on resumes its OWN last
  // mode; guessing "cool" first gave a heating user the air conditioning.
  power_on_resumes_the_device_mode() {
    const services = (features) => {
      const hass = makeHass({ state: "off", attrs: { supported_features: features } });
      makeCard({}, hass)._power();
      return hass.calls.map((c) => `${c.service}${c.data.hvac_mode ? ":" + c.data.hvac_mode : ""}`);
    };
    // 128 = TURN_ON, 256 = TURN_OFF, 1 = TARGET_TEMPERATURE.
    return { turnOn: services(1 | 128 | 256), noTurnOn: services(1) };
  },
  power_off_uses_turn_off_when_supported() {
    const services = (features) => {
      const hass = makeHass({ state: "heat", attrs: { supported_features: features } });
      makeCard({}, hass)._power();
      return hass.calls.map((c) => `${c.service}${c.data.hvac_mode ? ":" + c.data.hvac_mode : ""}`);
    };
    return { turnOff: services(1 | 128 | 256), noTurnOff: services(1) };
  },
  // Closing the popup (Escape) within the send delay must not lose the press.
  press_then_close_still_sends() {
    const hass = makeHass();
    const card = makeCard({}, hass);
    card._bump(1);
    card.disconnectedCallback();
    return setTemps(hass);
  },
  // A rejected write must not leave its value on screen as if it were the target.
  async rejected_write_drops_the_pending_target() {
    const hass = makeHass();
    hass.callService = (domain, service, data) => {
      hass.calls.push({ domain, service, data });
      return Promise.reject(new Error("rejected"));
    };
    const card = makeCard({}, hass);
    card._bump(1);
    flushTimers(1000);
    await settle();
    return { unhandled: unhandled.length, pending: card._pending, shows25:card.shadowRoot.getElementById("targetBox").innerHTML.includes(">25°") };
  },
  // A write HA never confirms stops standing in after 15 s, and the screen says so
  // without waiting for some unrelated state change.
  pending_expiry_redraws() {
    const hass = makeHass();
    const card = makeCard({}, hass);
    card._bump(1);
    flushTimers(1000);
    flushTimers();
    return card.shadowRoot.getElementById("targetBox").innerHTML.includes(">25°");
  },
  // An entity with a half-degree step (an Airzone Aidoo) must reach half degrees.
  half_degree_step() {
    const hass = makeHass({ state: "heat", attrs: { temperature: 21, target_temp_step: 0.5 } });
    const card = makeCard({}, hass);
    card._bump(1);
    const dial = card.shadowRoot.getElementById("targetBox").innerHTML;
    flushTimers(1000);
    const tile = makeCard({ layout: "tile" }, makeHass({ state: "heat", attrs: { temperature: 21.5, target_temp_step: 0.5 } }));
    return { sent: setTemps(hass), dial: dial.includes(">21.5°"), tile: tile.shadowRoot.getElementById("tsub").textContent.includes("21.5°") };
  },
  // In range mode the bump moves the high end: it must never cross the low one.
  range_high_never_below_low() {
    const hass = makeHass({ state: "heat_cool", attrs: {
      temperature: null, target_temp_low: 22, target_temp_high: 23, hvac_modes: ["off", "heat_cool"] } });
    const card = makeCard({}, hass);
    card._bump(-1); card._bump(-1);
    flushTimers(1000);
    return hass.calls.filter((c) => c.service === "set_temperature").map((c) => c.data);
  },
};

const only = process.argv[2];
const out = {};
(async () => {
  for (const [name, fn] of Object.entries(scenarios)) {
    if (only && only !== name) continue;
    timers.clear();
    try { out[name] = await fn(); } catch (e) { out[name] = { error: String(e && e.stack ? e.stack.split("\n")[0] : e) }; }
  }
  process.stdout.write(JSON.stringify(out));
})();

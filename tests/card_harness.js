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
const timers = [];
global.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
global.clearTimeout = (id) => { if (id && timers[id - 1]) timers[id - 1].fn = null; };
const flushTimers = () => { for (const t of timers.splice(0)) if (t.fn) t.fn(); };
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
    flushTimers();
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
  // Attribute values from any climate entity end up in innerHTML: they must be escaped.
  fan_mode_markup_is_escaped() {
    const hass = makeHass({ attrs: { fan_modes: ['<img src=x onerror=1>'], fan_mode: null } });
    const card = makeCard({}, hass);
    return card.shadowRoot.getElementById("fanSel").innerHTML.includes("<img");
  },
};

const only = process.argv[2];
const out = {};
for (const [name, fn] of Object.entries(scenarios)) {
  if (only && only !== name) continue;
  try { out[name] = fn(); } catch (e) { out[name] = { error: String(e && e.stack ? e.stack.split("\n")[0] : e) }; }
}
realSetTimeout(() => { process.stdout.write(JSON.stringify(out)); }, 0);

import { FLAGS } from "./format.js";

const state = {
  aspect: "health",
  view: "fields",
  selected: null,
  list: [],
  filters: { flags: new Set(FLAGS), crop: "all", zone: "all", check: "any", query: "" },
  frame: { index: 0, playing: false, speed: 1, opacity: 0.85 },
};

const bus = new EventTarget();

export const store = {
  get() {
    return state;
  },
  set(patch) {
    Object.assign(state, patch);
    bus.dispatchEvent(new CustomEvent("change", { detail: Object.keys(patch) }));
  },
  setFilters(patch) {
    store.set({ filters: { ...state.filters, ...patch } });
  },
  setFrame(patch) {
    store.set({ frame: { ...state.frame, ...patch } });
  },
  on(handler) {
    bus.addEventListener("change", (event) => handler(state, event.detail));
  },
};

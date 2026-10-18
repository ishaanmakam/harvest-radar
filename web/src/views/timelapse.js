import { fmtDate, fmtPct } from "../format.js";

/**
 * The time-lapse bar at the bottom of the map: scrubber with date and clear fraction, play/pause,
 * speed and opacity. Outside the Time-lapse view the map shows the latest pass at full opacity.
 */
export function createTimelapse({ data, store, mapApi, els }) {
  const frames = data.frames?.frames || [];
  const enabled = frames.length > 0 && Boolean(data.frames?.bounds);
  let timer = null;
  let active = false;

  if (enabled) {
    for (const frame of frames) {
      const img = new Image();
      img.src = `${data.framesBase}/${frame.rgb}`;
    }
    store.setFrame({ index: frames.length - 1 });
    els.slider.max = String(frames.length - 1);
    els.slider.addEventListener("input", () => store.setFrame({ index: Number(els.slider.value), playing: false }));
    els.play.addEventListener("click", () => store.setFrame({ playing: !store.get().frame.playing }));
    els.prev.addEventListener("click", () => step(-1));
    els.next.addEventListener("click", () => step(1));
    els.speed.addEventListener("change", () => store.setFrame({ speed: Number(els.speed.value) }));
    els.opacity.addEventListener("input", () => store.setFrame({ opacity: Number(els.opacity.value) }));
  }

  function step(delta) {
    const n = frames.length;
    store.setFrame({ index: (store.get().frame.index + delta + n) % n, playing: false });
  }

  function apply() {
    const frame = store.get().frame;
    const index = Math.max(0, Math.min(frames.length - 1, frame.index));
    const current = frames[index];
    els.date.textContent = fmtDate(current.date, "long");
    const parts = [`pass ${index + 1} of ${frames.length}`];
    if (current.clear_frac != null) parts.push(`${fmtPct(current.clear_frac * 100)} clear`);
    els.clear.textContent = parts.join(" / ");
    els.slider.value = String(index);
    els.slider.setAttribute("aria-valuetext", fmtDate(current.date, "long"));
    els.opacity.value = String(frame.opacity);
    els.speed.value = String(frame.speed);
    els.play.textContent = frame.playing ? "Pause" : "Play";
    els.play.setAttribute("aria-pressed", String(frame.playing));
    mapApi.setFrame(index, frame.opacity);
  }

  function syncTimer() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    const frame = store.get().frame;
    if (!active || !frame.playing) return;
    timer = setInterval(() => {
      store.setFrame({ index: (store.get().frame.index + 1) % frames.length });
    }, 900 / frame.speed);
  }

  return {
    enabled,
    enter() {
      if (!enabled) return;
      active = true;
      els.box.hidden = false;
      mapApi.setDimmed(true);
      mapApi.setFrameFade(false);
      apply();
      syncTimer();
    },
    leave() {
      if (!enabled) return;
      active = false;
      if (timer) clearInterval(timer);
      timer = null;
      els.box.hidden = true;
      if (store.get().frame.playing) store.setFrame({ playing: false });
      mapApi.setDimmed(false);
      mapApi.setFrameFade(true);
      mapApi.setFrame(frames.length - 1, 1);
    },
    update(keys) {
      if (!active || !keys.includes("frame")) return;
      apply();
      syncTimer();
    },
  };
}

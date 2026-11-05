// The replay timeline of every game: start, step back, play / pause, step forward, end, speed, position, a scrub track,
// the events of the shown position and closing back to the result. Frames come from the host (`load`), which may be slow:
// a request in flight shows "loading", and a newer choice replaces an older one.
import { button, element } from "../dom";

export interface ReplayBarOptions {
  /** Shows position `n`; resolves when it is on screen (rejects when it could not be read). */
  load(position: number): Promise<void>;
  /** Leaves the replay (back to the finished table). */
  close(): void;
}
export interface ReplayBar {
  readonly element: HTMLElement;
  /** The frame now on screen: its position, the number of positions and a short text of what happened. */
  shown(position: number, total: number, events: string): void;
  /** Marks on the track (for example a colour set, a hotel, a trade); a click goes to that position. */
  setMarks(marks: ReplayMark[], legend: [string, string][]): void;
  dispose(): void;
}
export interface ReplayMark { position: number; tone: string; label: string }

const SPEEDS = [1, 2, 4, 8, 16];
const BASE_MS = 900;

export function mountReplayBar(options: ReplayBarOptions): ReplayBar {
  const root = element("div", "timeline tc-replay");
  root.setAttribute("role", "group");
  root.setAttribute("aria-label", "Replay");
  const row = element("div", "tl-btns");
  const ctl = (icon: string, title: string, fn: () => void, cls = "btn sm") => {
    const b = button(icon, fn, cls);
    b.title = title;
    b.setAttribute("aria-label", title);
    return b;
  };
  let position = -1, total = 1, playing = false, wanted = 0, disposed = false, speed = 1;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const start = ctl("⏮", "Start (Home)", () => { pause(); go(0); });
  const back = ctl("◀", "Previous position (←)", () => { pause(); go(wanted - 1); });
  const play = ctl("▶", "Play / pause (Space)", () => toggle(), "btn pri sm");
  const fwd = ctl("▶▶", "Next position (→)", () => { pause(); go(wanted + 1); });
  const end = ctl("⏭", "End (End)", () => { pause(); go(total - 1); });
  const speedSelect = element("select");
  speedSelect.title = "Playback speed";
  speedSelect.setAttribute("aria-label", "Playback speed");
  for (const s of SPEEDS) speedSelect.append(Object.assign(element("option", "", `${s}×`), { value: String(s) }));
  speedSelect.onchange = () => (speed = Number(speedSelect.value));
  const pos = element("span", "tl-pos muted");
  pos.setAttribute("role", "status");
  const close = button("✕ Close replay", () => { pause(); options.close(); }, "btn sm tl-close");
  const legendBox = element("span", "tl-legend");
  row.append(start, back, play, fwd, end, speedSelect, pos, legendBox, close);
  const track = element("div", "tl-track");
  const scrub = element("input");
  scrub.type = "range";
  scrub.min = "0";
  scrub.setAttribute("aria-label", "Replay position");
  let dragging: ReturnType<typeof setTimeout> | undefined;
  scrub.addEventListener("input", () => {
    playing = false; clearTimeout(timer); play.textContent = "▶"; pos.textContent = `decision ${Number(scrub.value) + 1} / ${total}`;
    clearTimeout(dragging);
    dragging = setTimeout(() => void go(Number(scrub.value)), 60);
  });
  scrub.addEventListener("change", () => { clearTimeout(dragging); void go(Number(scrub.value)); });
  const marksBox = element("div", "mks");
  marksBox.addEventListener("click", e => {
    const n = (e.target as HTMLElement).closest<HTMLElement>("[data-n]")?.dataset.n;
    if (n != null) { pause(); void go(Number(n)); }
  });
  track.append(marksBox, scrub);
  const events = element("div", "tl-events");
  events.setAttribute("aria-live", "polite");
  // The track appears with the first frame, when the number of positions is known.
  root.append(row, events);

  function render() {
    const at = Math.min(wanted, total - 1);
    scrub.max = String(Math.max(0, total - 1));
    scrub.value = String(at);
    pos.textContent = `decision ${at + 1} / ${total}`;
    start.disabled = back.disabled = at <= 0;
    end.disabled = fwd.disabled = at >= total - 1;
    play.textContent = playing ? "⏸" : "▶";
    root.classList.toggle("loading", wanted !== position);
  }
  async function go(n: number) {
    n = Math.max(0, Math.min(total - 1, n));
    wanted = n;
    render();
    try {
      await options.load(n);
    } catch {
      if (wanted === n) pause();
    }
  }
  function pause() {
    playing = false;
    clearTimeout(timer);
    render();
  }
  function toggle() {
    if (playing) return pause();
    if (wanted >= total - 1) return;
    playing = true;
    render();
    void loop();
  }
  async function loop() {
    while (playing && !disposed && wanted < total - 1) {
      const began = performance.now();
      await go(wanted + 1);
      if (!playing || disposed) return;
      const rest = BASE_MS / speed - (performance.now() - began);
      if (rest > 0) await new Promise<void>(r => (timer = setTimeout(r, rest)));
    }
    if (playing) pause();
  }
  const onKey = (e: KeyboardEvent) => {
    if (disposed || !root.isConnected) return;
    const t = e.target as HTMLElement;
    if ((t.tagName === "INPUT" && (t as HTMLInputElement).type !== "range") || t.tagName === "SELECT" || t.tagName === "TEXTAREA") return;
    if (e.key === " " && t.tagName !== "BUTTON") { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowRight" && t.tagName !== "INPUT") { pause(); void go(wanted + 1); }
    else if (e.key === "ArrowLeft" && t.tagName !== "INPUT") { pause(); void go(wanted - 1); }
    else if (e.key === "Home") { pause(); void go(0); }
    else if (e.key === "End") { pause(); void go(total - 1); }
  };
  window.addEventListener("keydown", onKey);
  render();
  return {
    element: root,
    shown(n, count, text) {
      // The host shows only the newest requested frame, so the shown position is the wanted one.
      position = wanted = n;
      total = Math.max(1, count);
      if (!track.isConnected) root.insertBefore(track, events);
      events.textContent = text;
      render();
    },
    setMarks(marks, legend) {
      const span = Math.max(1, total - 1);
      marksBox.replaceChildren(...marks.map(m => {
        const mark = element("button", `mk tone-${m.tone}`);
        mark.type = "button";
        mark.dataset.n = String(m.position);
        mark.title = m.label;
        mark.setAttribute("aria-label", `${m.label} (position ${m.position + 1})`);
        mark.style.left = `${(m.position / span) * 100}%`;
        return mark;
      }));
      legendBox.replaceChildren(...legend.map(([tone, label]) => { const item = element("span"); item.append(element("i", `tone-${tone}`), label); return item; }));
    },
    dispose() {
      disposed = true;
      playing = false;
      clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
      root.remove();
    },
  };
}

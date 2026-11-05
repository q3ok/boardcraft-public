import { button } from "./dom";
import { mountNavigation, type NavigationItem } from "./ui/nav";

/** Platform pages of the shared bar; "watch" and "settings" remain accepted from older hosts (Play and the colour page). */
export type Page = "play" | "tournaments" | "rankings" | "replays" | "watch" | "settings";
export interface NavigationOptions {
  current: Page;
  go: (page: Page) => void;
  /** Shown only when the host application embeds Boardcraft and supplied a way back. */
  exit?: () => void;
  sections?: NavigationItem[];
  selectSection?: (id: string) => void;
  rules?: () => void;
}
const pages: NavigationItem[] = [
  { id: "play", label: "🎲 Play" }, { id: "tournaments", label: "⚔ Tournaments" },
  { id: "rankings", label: "🏆 Rankings" }, { id: "replays", label: "▶ Replays" },
];

/** Platform sections and optional host entries share the same shell: pages, the colour menu, then Rules. */
export function renderNavigation({ current, go, exit, sections = [], selectSection, rules }: NavigationOptions) {
  const bar = document.createElement("header");
  mountNavigation(bar, { brand: "Boardcraft", emblem: new URL("../public/boardcraft-emblem.png", import.meta.url).href,
    active: current === "watch" || current === "settings" ? "play" : current, items: [...pages, ...sections],
    select: id => { if (sections.some(s => s.id === id)) selectSection?.(id); else if (id !== current) go(id as Page); },
  });
  bar.classList.add("bc-nav");
  bar.querySelector("nav")!.setAttribute("aria-label", "Boardcraft sections");
  if (rules) bar.append(button("📖 Rules", rules, "bc-nav-rules"));
  if (exit) bar.append(button("← Back", exit, "bc-nav-exit"));
  return bar;
}

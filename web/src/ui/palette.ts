import { button, element } from "../dom";
export const PALETTES = [
  { id: "biel", name: "White + accent" }, { id: "stol", name: "Table green" },
  { id: "granat", name: "Navy & gold" }, { id: "ciemny", name: "Modern dark" }, { id: "propozycja", name: "Emblem" },
] as const;
export type PaletteId = typeof PALETTES[number]["id"];
export function currentPalette(): PaletteId {
  try { const saved = localStorage.getItem("boardcraft-palette"); if (PALETTES.some(p => p.id === saved)) return saved as PaletteId; } catch { /* Storage is optional. */ }
  return "granat";
}
export function applyPalette(id: PaletteId = currentPalette()) { document.documentElement.dataset.palette = id; }
export function mountPalette(root: HTMLElement) {
  const group = element("div", "bc-palette"); group.setAttribute("aria-label", "Colours");
  const buttons = PALETTES.map(p => {
    const b = button(p.name, () => {
      try { localStorage.setItem("boardcraft-palette", p.id); } catch { /* Apply for this visit. */ }
      applyPalette(p.id); update(p.id);
    });
    b.dataset.palette = p.id; group.append(b); return b;
  });
  function update(id: PaletteId) { for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.palette === id)); }
  update(currentPalette()); root.append(group);
}

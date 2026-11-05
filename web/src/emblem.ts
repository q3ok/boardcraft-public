export const emblemUrl = new URL(
  "../public/boardcraft-emblem.png",
  import.meta.url,
).href;
let source: HTMLImageElement | undefined;
export function emblemSource() {
  if (!source) {
    source = new Image();
    source.src = emblemUrl;
  }
  return source;
}
export function emblemIcon(className = "bc-emblem") {
  const img = document.createElement("img");
  img.src = emblemUrl;
  img.alt = "Emblem";
  img.className = className;
  return img;
}

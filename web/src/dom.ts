export function button(label: string, fn: () => void, className = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.className = className;
  b.addEventListener("click", fn);
  return b;
}
export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = "",
  text = "",
) {
  const e = document.createElement(tag);
  e.className = className;
  e.textContent = text;
  return e;
}

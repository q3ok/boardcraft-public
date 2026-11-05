/** Printed game marks from the shared platform design. */
export function gameIcon(gameId: string, size = 18): HTMLElement {
  const host = document.createElement("span"); host.className = "bc-game-icon";
  const content = gameId === "rentcraft"
    ? '<rect class="gi-rc-body" x="2.5" y="2.5" width="19" height="19" rx="2"/><rect class="gi-rc-roof" x="2.5" y="2.5" width="19" height="5"/><path class="gi-rc-house" d="M6 18v-6l6-4.2 6 4.2v6z"/><rect class="gi-rc-door" x="10.3" y="13.5" width="3.4" height="4.5"/>'
    : '<rect class="gi-dice-body" x="3" y="3" width="18" height="18" rx="5"/><circle class="gi-dice-pip" cx="8.5" cy="8.5" r="1.7"/><circle class="gi-dice-pip" cx="15.5" cy="15.5" r="1.7"/><circle class="gi-dice-pip" cx="15.5" cy="8.5" r="1.7"/><circle class="gi-dice-pip" cx="8.5" cy="15.5" r="1.7"/>';
  host.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${content}</svg>`;
  return host;
}

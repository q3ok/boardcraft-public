// Side panel of the Rentcraft table: players, the decision panel (actions locked after a click), offer-window, deadline,
// debt, autopilot and takeover notes, seat tags at the board edges and the drawn-card show.
import type { Action, DebtSummary } from "../../play-types";
import { windowLabel } from "./log";
import { ANIMAL_EMOJI, esc, money, mortgageCost, netWorth, seatAnimal, seatClass } from "./format";
import type { Animal } from "./pieces";
import type { RentBoard, RentState } from "./types";

const MANAGEMENT = new Set(["build", "sell_building", "mortgage", "unmortgage"]);
const PRIMARY = new Set(["roll", "buy", "end_turn"]);

export interface Table {
  board: RentBoard;
  state: RentState;
  names: string[];
  kinds: string[];
  /** The viewer's seat; null for an observer. */
  me: number | null;
  /** Each seat's piece chosen at the table (default: the seat's own animal). */
  animals?: Animal[];
}

/** Actions in groups; `takeoverSpace`: paying off a taken-over mortgaged space is a main action, not management. */
export function groupActions(actions: Action[], takeoverSpace: number | null = null) {
  const space = (a: Action) => a.params?.space as number | undefined;
  const managed = (a: Action) => MANAGEMENT.has(a.kind) && !(a.kind === "unmortgage" && space(a) === takeoverSpace);
  const bids = actions.filter(a => a.kind === "bid");
  const manage = actions.filter(managed).sort((a, b) => (space(a) ?? 0) - (space(b) ?? 0) || a.kind.localeCompare(b.kind));
  const main = actions.filter(a => a.kind !== "bid" && !managed(a));
  return { main, bids, manage };
}

/** Player list; `autopilot`: the seat that autopilot plays for (only the player's own view knows it). */
export function playersHtml(t: Table, autopilot: number | null = null): string {
  const { state, names, kinds, me } = t;
  return state.players.map((p, i) => {
    const cls = ["pl", i === state.current && !p.bankrupt ? "act" : "", p.bankrupt ? "out" : "", i === me ? "me" : ""].join(" ");
    const status = (p.bankrupt ? '<span class="tag out">out</span>' : p.in_jail ? '<span class="tag">in jail</span>' : "")
      + (i === autopilot && !p.bankrupt ? '<span class="tag auto">autopilot</span>' : "");
    return `<div class="${cls}" data-seat="${i}" role="button" tabindex="0" title="View the table from seat: ${esc(names[i])}">
      <span class="d pcbg ${seatClass(i)}"></span><span class="an">${ANIMAL_EMOJI[t.animals?.[i] ?? seatAnimal(i)]}</span>
      <span class="n"><b>${esc(names[i])}</b>${kinds[i] ? `<span class="tag">${esc(kinds[i])}</span>` : ""}${status}</span>
      <span class="m"><b data-cash="${i}">${money(p.cash)}</b><small>net worth ${money(netWorth(t.board, state, i))}</small></span>
      <span class="cam" aria-hidden="true">🎥</span></div>`;
  }).join("");
}

/** Tags at the board edges: who sits on which side (colour, animal, name) and how much cash they have. */
export function seatTagsHtml(t: Table): string {
  const { state, names, me } = t;
  return state.players.map((p, i) => {
    const cls = ["seat-tag", seatClass(i), i === me ? "me" : "", i === state.current && !p.bankrupt ? "act" : "", p.bankrupt ? "out" : ""].join(" ");
    return `<div class="${cls}" data-tag="${i}"><span class="an">${ANIMAL_EMOJI[t.animals?.[i] ?? seatAnimal(i)]}</span>
      <span class="nm">${esc(names[i])}${i === me ? " (you)" : ""}</span><b class="cash">${p.bankrupt ? "out" : money(p.cash)}</b></div>`;
  }).join("");
}

/** What is happening now, in one sentence, from the viewer's side. */
export function phaseText(t: Table): string {
  const { board, state: s, names, me } = t;
  if (me != null && s.players[me]?.bankrupt) return "You are out of the game. You can keep watching.";
  if (s.result || s.phase === "game_over") return "The game is over.";
  const actor = s.acting_seat ?? s.current;
  const mine = me != null && actor === me;
  const who = mine ? "You" : names[actor] ?? `Player ${actor + 1}`;
  const space = (i: number | null | undefined) => (i == null ? "" : board.spaces[i]?.name ?? "");
  switch (s.phase) {
    case "pre_roll": {
      if (s.players[actor]?.in_jail) return mine ? "You are in jail: pay the fine, use a card or roll for doubles." : `${who} is in jail and decides how to get out.`;
      return mine ? "Your turn - roll the dice." : `${who}'s turn.`;
    }
    case "buy_decision": {
      const i = s.pending_buy ?? s.players[actor]?.position;
      return `${who} landed on ${space(i)}: buy it for ${money(board.spaces[i!]?.price ?? 0)} or send it to auction.`;
    }
    case "auction": {
      const a = s.auction;
      if (!a) return "Auction.";
      const high = a.high_bidder == null ? "no bid yet" : `highest ${money(a.high_bid)} by ${a.high_bidder === me ? "you" : names[a.high_bidder]}`;
      return `Auction for ${space(a.space)}: ${high}.${mine ? " Raise the bid or leave the auction." : ""}`;
    }
    case "raise_funds": {
      const d = s.debts?.[0];
      if (!d) return `${who} must raise money.`;
      const to = d.creditor == null ? "the bank" : d.creditor === me ? "you" : names[d.creditor];
      return `${who} ${mine ? "have" : "has"} to pay ${money(d.amount)} to ${to}: mortgage, sell buildings or trade to raise it.`;
    }
    case "trade_response": {
      const o = s.trade?.offer;
      if (!o) return `${who} answers a trade offer.`;
      return mine ? `${names[o.proposer]} offers you a trade.` : `${names[o.target]} answers a trade offer from ${names[o.proposer]}.`;
    }
    case "post_roll": return mine ? "Build, mortgage or trade - then end your turn." : `${who} manages property before ending the turn.`;
    case "trade_window": return `Offer window (${windowLabel(s.window?.kind)}): ${mine ? "you may" : `${who} may`} propose a trade.`;
    case "mortgage_takeover": {
      const pending = s.pending_takeovers?.[0];
      return `${who} ${mine ? "decide" : "decides"} about a taken-over mortgaged space: ${space(pending?.[1])}.`;
    }
    default: return s.phase.replaceAll("_", " ");
  }
}

const EFFECT: Record<string, string> = {
  autopilot: "autopilot takes over the game (you come back with the “Back to the game” button)",
  resign: "you surrender and leave the game",
  declare_bankruptcy: "you go bankrupt",
  pass: "the game passes for you",
};

/** Decision deadline indicator: "⏱ Decision deadline: 0:42 · then: …"; urgent at 30 s or less. */
export function deadlineInfoHtml(timed: boolean, effect: string | undefined): string {
  if (!timed) return "";
  const then = effect ? EFFECT[effect] ?? effect.replaceAll("_", " ") : "";
  // The seconds are filled in by the screen's clock, so a poll with the same deadline does not redraw the panel.
  return `<div class="deadline-info">⏱ Decision deadline: <b data-deadline-left></b>${then ? ` · then: ${esc(then)}` : ""}</div>`;
}

/** Offer window indicator, e.g. "Offer window: after a purchase - 3 s · Ann decides · Bob will pay $28". */
export function windowInfoHtml(t: Table, timed: boolean): string {
  const w = t.state.window;
  if (!w) return "";
  const asked = t.state.acting_seat;
  const pay = (w.payments as [number, number | null, number][] | undefined ?? [])
    .map(p => `${esc(p[0] === t.me ? "you" : t.names[p[0]])} will pay ${money(p[2])}`).join(", ");
  const wait = timed ? ` - <span data-left></span> s` : "";
  const who = asked != null ? ` · ${asked === t.me ? "you decide" : `${esc(t.names[asked])} decides`}` : "";
  return `<div class="window-info">🪟 Offer window: ${esc(windowLabel(w.kind))}${wait}${who}${pay ? ` · ${pay}` : ""}</div>`;
}

/** The debt of the deciding player and, when the game offers it, the full bank liquidation button. */
export function debtInfoHtml(t: Table, canLiquidate: boolean, debt?: DebtSummary): string {
  const s = t.state;
  const d = s.debts?.[0];
  if (!d || t.me == null || d.debtor !== t.me || s.phase !== "raise_funds") return "";
  const to = d.creditor == null ? "the bank" : esc(t.names[d.creditor]);
  const cash = s.players[t.me].cash;
  // The server's summary of what the bank can raise (the shared design's "The bank is enough / not enough").
  const line = debt
    ? debt.bank_enough
      ? `The bank is enough: mortgages right now ${money(debt.mortgage_now)}, building sales ${money(debt.building_sales)}, mortgages after the sales ${money(debt.mortgage_after_sales)} - up to ${money(debt.max_cash_from_bank)} in all (debt ${money(debt.amount)}).`
      : `The bank is not enough. Full liquidation and mortgages give at most ${money(debt.max_cash_from_bank)}. You still need ${money(debt.needed_from_trades)} from sales to players.`
    : `You owe ${money(d.amount)} to ${to} and have ${money(cash)}. Mortgage spaces, sell buildings or sell to a player to raise the rest.`;
  const btn = canLiquidate ? ` <button type="button" class="btn sm liquidate-btn" title="Mortgages first, then building sales at half price - until the debt is paid or the bank has nothing more to give">🏦 Full bank liquidation</button>` : "";
  return `<div class="debt-info">💸 ${line}${btn}</div>`;
}

/** Autopilot plays for me (after a timeout): a badge and the "Back to the game" button. */
export function autopilotHtml(on: boolean, canReclaim: boolean, round?: number): string {
  if (!on) return "";
  const since = round != null ? `You ran out of time in round ${round} - autopilot has been playing for you since.` : "You ran out of time - autopilot is playing for you.";
  return `<div class="autopilot-info"><span class="tag auto">autopilot</span> ${since}${canReclaim ? ` <button type="button" class="btn sm pri reclaim-btn">Back to the game</button>` : ""}</div>`;
}

/** Row in the decision panel at a taken-over space decision: the payoff, what is missing, "Show the decision". */
export function takeoverInfoHtml(t: Table, space: number, open: boolean): string {
  const { interest, payoff } = mortgageCost(t.board, space);
  const cash = t.me == null ? 0 : t.state.players[t.me].cash;
  const missing = Math.max(0, payoff - cash);
  return `<div class="trade-info">🏦 ${esc(t.board.spaces[space].name)}: payoff ${money(payoff)}${missing ? ` - ${money(missing)} short` : ""}, interest ${money(interest)}${open ? ` <button type="button" class="btn sm show-takeover">Show the decision</button>` : ""}</div>`;
}

export type TradeButtonMode = "compose" | "request" | "pending" | null;

/** The "Propose a trade" button: an offer (my decision), a request for a trade, or a request already waiting. */
export function tradeButtonHtml(mode: TradeButtonMode): string {
  if (mode === null) return "";
  if (mode === "pending") return `<button type="button" class="btn trade-btn" data-mode="pending" disabled>🤝 Requested - wait for the nearest moment</button>`;
  const title = mode === "request" ? ` title="The game does not interrupt a move halfway: at the nearest safe point you get a decision to propose a trade"` : "";
  return `<button type="button" class="btn trade-btn" data-mode="${mode}"${title}>🤝 Propose a trade</button>`;
}

export class ActionPanel {
  private locked = false;
  private manageOpen = false;

  constructor(private el: HTMLElement, private onAct: (id: string) => void) {
    el.addEventListener("click", e => {
      const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-id]");
      if (!btn || btn.disabled || this.locked) return;
      this.locked = true;
      el.querySelectorAll<HTMLButtonElement>("button[data-id]").forEach(b => (b.disabled = true));
      this.onAct(btn.dataset.id!);
    });
    el.addEventListener("toggle", e => {
      const d = e.target as HTMLDetailsElement;
      if (d.classList?.contains("manage")) this.manageOpen = d.open;
    }, true);
  }

  /** Unlocks the buttons after a rejected move. */
  unlock() {
    this.locked = false;
    this.el.querySelectorAll<HTMLButtonElement>("button[data-id]").forEach(b => (b.disabled = false));
  }

  render(actions: Action[], phase: string, yourTurn: boolean, extra = "", takeoverSpace: number | null = null, labels: (a: Action) => string = a => a.label) {
    this.locked = false;
    const { main, bids, manage } = groupActions(actions, takeoverSpace);
    const btn = (a: Action, cls = "") => `<button type="button" class="btn ${cls}" data-id="${esc(a.id)}">${esc(labels(a))}</button>`;
    let html = `<div class="phase">${esc(phase)}</div>${extra}`;
    if (yourTurn) {
      if (bids.length) html += `<div class="bids">${bids.map(a => btn(a, "bid")).join("")}</div>`;
      if (main.length) html += `<div class="btns">${main.map(a => btn(a, a.kind === "declare_bankruptcy" ? "bankrupt" : PRIMARY.has(a.kind) ? "pri" : "")).join("")}</div>`;
      if (manage.length) {
        const open = this.manageOpen || main.length === 0 ? " open" : "";
        html += `<details class="manage"${open}><summary>Manage property (${manage.length})</summary>${manage.map(a => btn(a, "small")).join("")}</details>`;
      }
    }
    if (this.el.dataset.html !== html) {
      this.el.dataset.html = html;
      this.el.innerHTML = html;
    } else this.unlock();
  }
}

const DECK = {
  opportunity: { name: "Opportunity", glyph: "?", cls: "chance" },
  community_fund: { name: "Community Fund", glyph: "✉", cls: "chest" },
} as const;

export interface CardDraw { deck: string; text: string; player: number | null }

export function cardHtml(c: CardDraw, names: string[]): string {
  const d = DECK[c.deck as keyof typeof DECK] ?? DECK.opportunity;
  const who = c.player == null ? "" : names[c.player] ?? "";
  return `<div class="cs-card ${d.cls}" role="dialog" aria-label="${d.name} card"><div class="cs-inner">
      <div class="cs-back"><span>${d.glyph}</span><b>${d.name.toUpperCase()}</b></div>
      <div class="cs-front"><div class="cs-hd">${d.glyph} ${d.name}</div><div class="cs-text">${esc(c.text)}</div>
        ${who ? `<div class="cs-who"><i class="dot pcbg ${seatClass(c.player!)}"></i>${esc(who)} draws</div>` : ""}</div>
    </div></div>`;
}

/** Queue of shows: the cards appear one after another, each for a few seconds; a click closes the current one. */
export class CardShow {
  private queue: { c: CardDraw; names: string[] }[] = [];
  private el: HTMLDivElement | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private host: HTMLElement, private ms = 4200) {}

  push(cards: CardDraw[], names: string[]) {
    for (const c of cards) this.queue.push({ c, names });
    if (!this.el) this.next();
  }

  get showing() { return this.el !== null; }

  private next() {
    const item = this.queue.shift();
    if (!item) return;
    const el = document.createElement("div");
    el.className = "card-show";
    el.innerHTML = cardHtml(item.c, item.names);
    el.addEventListener("click", () => this.dismiss());
    this.host.appendChild(el);
    this.el = el;
    this.timer = setTimeout(() => this.dismiss(), this.ms);
  }

  dismiss() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const el = this.el;
    this.el = null;
    if (el) {
      el.classList.add("out");
      setTimeout(() => el.remove(), 250);
    }
    this.next();
  }

  clear() {
    this.queue = [];
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.el?.remove();
    this.el = null;
  }
}

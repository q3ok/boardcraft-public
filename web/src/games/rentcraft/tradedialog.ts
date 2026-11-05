// Trade window: a new offer, the answer to an offer (accept / reject / counter-offer), the taken-over mortgaged space, hints.
import { deedHtml, ownerBadge } from "./deed";
import { GROUP_NAME, esc, groupClass, groupMembers, money, mortgageCost, seatClass, tradeableSpaces } from "./format";
import { offerTerms } from "./log";
import type { Table } from "./panel";
import type { Offer, RentBoard, RentState } from "./types";
import type { Action } from "../../play-types";

/** Which limit the proposal counter refers to: the turn, the debt, the auction, the offer window or the takeover. */
const TRADE_SCOPE: Record<string, string> = {
  pre_roll: "this turn", post_roll: "this turn", raise_funds: "for this debt", auction: "in this auction",
  trade_window: "in this offer window", mortgage_takeover: "for this taken-over space", buy_decision: "this turn",
};
const MANAGE_KINDS = new Set(["build", "sell_building", "mortgage", "unmortgage"]);

export interface TradeDraft {
  target: number;
  give_spaces: number[];
  get_spaces: number[];
  give_cash: number;
  get_cash: number;
  give_cards: number;
  get_cards: number;
}
export interface TradeLimits { perTurn: number; exchanges: number }

function fullGroups(board: RentBoard, owned: Set<number>): Set<string> {
  const out = new Set<string>();
  for (const g of Object.keys(GROUP_NAME)) {
    const members = groupMembers(board, g);
    if (members.length && members.every(i => owned.has(i))) out.add(g);
  }
  return out;
}

/** Hints from the point of view of player `me` (gives give_*, gets get_*). */
export function tradeHints(t: Table, me: number, d: TradeDraft): string[] {
  const { board, state: st, names } = t;
  const owned = (pid: number) => new Set(st.owner.flatMap((o, i) => (o === pid ? [i] : [])));
  const mine = owned(me), theirs = owned(d.target);
  const mineAfter = new Set([...mine].filter(i => !d.give_spaces.includes(i)).concat(d.get_spaces));
  const theirsAfter = new Set([...theirs].filter(i => !d.get_spaces.includes(i)).concat(d.give_spaces));
  const out: string[] = [];
  const beforeMine = fullGroups(board, mine);
  for (const g of fullGroups(board, mineAfter)) if (!beforeMine.has(g)) out.push(`✓ You get a complete set of ${GROUP_NAME[g]} streets - you can build.`);
  const beforeTheirs = fullGroups(board, theirs);
  const name = names[d.target] ?? "A rival";
  for (const g of fullGroups(board, theirsAfter)) if (!beforeTheirs.has(g)) out.push(`⚠ ${name} gets a complete set of ${GROUP_NAME[g]} streets.`);
  const mortgaged = d.get_spaces.filter(i => st.mortgaged[i]);
  const interest = mortgaged.reduce((a, i) => a + mortgageCost(board, i).interest, 0);
  if (interest) {
    const payoff = mortgaged.reduce((a, i) => a + mortgageCost(board, i).payoff, 0);
    out.push(`For the mortgaged spaces you will pay ${money(interest)} interest or pay the mortgage off at once (${money(payoff)}) - you choose after the trade.`);
  }
  const value = (list: number[]) => list.reduce((a, i) => a + (board.spaces[i]?.price ?? 0), 0);
  out.push(`Value at board prices: you give ${money(value(d.give_spaces) + d.give_cash)}, you get ${money(value(d.get_spaces) + d.get_cash)}.`);
  out.push(`After the trade you have ${money(st.players[me].cash - d.give_cash + d.get_cash - interest)}.`);
  return out;
}

function miniCard(board: RentBoard, i: number, selected: boolean, disabled: boolean, mortgaged: boolean): string {
  const b = board.spaces[i];
  const title = disabled ? "There are buildings in this colour set - sell them first" : b.name;
  return `<button type="button" class="mc ${selected ? "sel" : ""} ${disabled ? "dim" : ""} ${mortgaged ? "mort" : ""}" data-space="${i}" title="${esc(title)}" aria-pressed="${selected}"${disabled ? " aria-disabled=\"true\"" : ""}>
    <div class="h ${groupClass(b.group, "station")}"></div><div class="n">${esc(b.name)}</div><div class="p">${money(b.price ?? 0)}</div></button>`;
}

const hintsHtml = (lines: string[]) => lines.map(h => `<div class="${h.startsWith("⚠") ? "warn" : h.startsWith("✓") ? "ok" : ""}">${esc(h)}</div>`).join("");
const jailCards = (st: RentState, pid: number) => st.players[pid]?.jail_cards?.length ?? 0;

export interface TradeHandlers {
  /** Sends a new offer or a counter-offer; a rejection keeps the dialog and its draft. */
  send(draft: TradeDraft, counter: boolean): Promise<void>;
  /** Plays a legal action of the current decision. */
  act(action: Action): void;
}

export class TradeDialog {
  private el: HTMLDivElement | null = null;
  private tip: HTMLDivElement | null = null;
  private draft!: TradeDraft;
  private counter = false;
  private openedFor = "";
  /** The decision during which the player manages property (the window is hidden until "Show the offer"/"Show the decision"). */
  private managing: string | null = null;
  private mode: "compose" | "respond" | "takeover" | null = null;
  /** The decision a compose window belongs to: it closes when that decision is gone. */
  composedFor: string | null = null;

  constructor(private host: HTMLElement, public me: number, private handlers: TradeHandlers) {}

  close() {
    this.el?.remove();
    this.el = null;
    this.mode = null;
    this.composedFor = null;
    this.hideCardTip();
  }
  get isOpen() { return this.el !== null; }
  get isCompose() { return this.mode === "compose"; }
  get isTakeover() { return this.mode === "takeover"; }
  get isRespond() { return this.mode === "respond"; }

  /** New offer (or counter-offer: counter = true, the recipient fixed). */
  compose(t: Table, limits: TradeLimits, decision: string | null, prefill?: Partial<TradeDraft>, counter = false) {
    const others = t.state.players.filter(p => p.id !== this.me && !p.bankrupt);
    this.counter = counter;
    this.composedFor = decision;
    this.draft = { target: others[0]?.id ?? 0, give_spaces: [], get_spaces: [], give_cash: 0, get_cash: 0, give_cards: 0, get_cards: 0, ...prefill };
    this.renderCompose(t, limits);
  }

  private renderCompose(t: Table, limits: TradeLimits, error = "") {
    const { board, state: st, names } = t;
    const d = this.draft;
    const me = st.players[this.me], them = st.players[d.target];
    const mineOk = new Set(tradeableSpaces(board, st, this.me)), theirsOk = new Set(tradeableSpaces(board, st, d.target));
    const list = (pid: number, ok: Set<number>, sel: number[]) =>
      board.spaces.filter(s => st.owner[s.index] === pid).map(s => miniCard(board, s.index, sel.includes(s.index), !ok.has(s.index), !!st.mortgaged[s.index])).join("")
      || `<span class="muted">no spaces</span>`;
    const who = this.counter
      ? `<span class="chip on"><i class="dot pcbg ${seatClass(d.target)}"></i>${esc(names[d.target])}</span>`
      : st.players.filter(p => p.id !== this.me && !p.bankrupt)
        .map(p => `<button type="button" class="chip ${p.id === d.target ? "on" : ""}" data-target="${p.id}" aria-pressed="${p.id === d.target}"><i class="dot pcbg ${seatClass(p.id)}"></i>${esc(names[p.id])}</button>`).join("");
    const footer = this.counter
      ? `Counter-offer (offer ${(st.trade?.exchanges ?? 0) + 1} of ${limits.exchanges} in this negotiation)`
      : `Proposal ${(st.trades_this_turn ?? 0) + 1} of ${limits.perTurn} ${TRADE_SCOPE[st.phase] ?? "this turn"}`;
    const myCards = jailCards(st, this.me), theirCards = jailCards(st, d.target);
    this.mount("compose", `
      <div class="tw-hd"><b>🤝 ${this.counter ? "Counter-offer to" : "Trade with"}:</b><div class="who">${who}</div><button type="button" class="btn sm tw-close" aria-label="Close">✕</button></div>
      <div class="tw-cols">
        <div class="tw-col"><h4>You give <small>click spaces</small></h4><div class="cards" data-side="give">${list(this.me, mineOk, d.give_spaces)}</div>
          <div class="extra"><label>Cash <input type="number" min="0" max="${me.cash}" step="10" data-f="give_cash" value="${d.give_cash}" aria-label="Give cash"> $</label>
          ${myCards ? `<label>“Get out of jail” cards <input type="number" min="0" max="${myCards}" data-f="give_cards" value="${d.give_cards}" aria-label="Give jail cards"></label>` : ""}</div></div>
        <div class="tw-swap" aria-hidden="true">⇄</div>
        <div class="tw-col"><h4>You get <small>${esc(names[d.target])}'s spaces</small></h4><div class="cards" data-side="get">${list(d.target, theirsOk, d.get_spaces)}</div>
          <div class="extra"><label>Cash <input type="number" min="0" max="${them?.cash ?? 0}" step="10" data-f="get_cash" value="${d.get_cash}" aria-label="Get cash"> $</label><span class="muted">${esc(names[d.target])} has ${money(them?.cash ?? 0)}</span>
          ${theirCards ? `<label>“Get out of jail” cards <input type="number" min="0" max="${theirCards}" data-f="get_cards" value="${d.get_cards}" aria-label="Get jail cards"></label>` : ""}</div></div>
      </div>
      <div class="hints">${hintsHtml(tradeHints(t, this.me, d))}</div>
      ${error ? `<div class="tw-error" role="alert">${esc(error)}</div>` : ""}
      <div class="tw-ft"><span class="muted">${footer}</span><button type="button" class="btn tw-close">Cancel</button><button type="button" class="btn pri tw-send">${this.counter ? "Send counter-offer" : "Send offer"}</button></div>`);
    const el = this.el!;
    this.bindCardTips(t);
    el.querySelectorAll<HTMLElement>("[data-target]").forEach(c => c.addEventListener("click", () => {
      this.draft = { ...this.draft, target: Number(c.dataset.target), get_spaces: [], get_cash: 0, get_cards: 0 };
      this.renderCompose(t, limits);
    }));
    el.querySelectorAll<HTMLElement>(".cards[data-side]").forEach(box => box.addEventListener("click", e => {
      const card = (e.target as HTMLElement).closest<HTMLElement>(".mc");
      if (!card || card.classList.contains("dim")) return;
      const i = Number(card.dataset.space);
      const key = box.dataset.side === "give" ? "give_spaces" : "get_spaces";
      const arr = this.draft[key];
      this.draft[key] = arr.includes(i) ? arr.filter(x => x !== i) : [...arr, i];
      this.renderCompose(t, limits);
    }));
    el.querySelectorAll<HTMLInputElement>("input[data-f]").forEach(inp => inp.addEventListener("change", () => {
      const val = Math.max(0, Math.min(Number(inp.max), Math.round(Number(inp.value) || 0)));
      (this.draft as unknown as Record<string, number>)[inp.dataset.f!] = val;
      this.renderCompose(t, limits);
    }));
    const send = el.querySelector<HTMLButtonElement>(".tw-send")!;
    send.addEventListener("click", async () => {
      if (send.disabled) return;
      el.querySelectorAll<HTMLInputElement>("input[data-f]").forEach(inp => {
        (this.draft as unknown as Record<string, number>)[inp.dataset.f!] = Math.max(0, Math.min(Number(inp.max), Math.round(Number(inp.value) || 0)));
      });
      send.disabled = true;
      const sent = this.el;
      try {
        await this.handlers.send({ ...this.draft }, this.counter);
        if (this.el === sent) this.close();
      } catch (e) {
        if (this.el === sent && this.mode === "compose") this.renderCompose(t, limits, (e as Error).message);
      }
    });
  }

  /** The answer to an offer the player received. Opens once per decision. */
  respond(t: Table, limits: TradeLimits, decision: string, actions: Action[], canCounter: boolean, onCounter: () => void) {
    const { board, state: st, names } = t;
    const tr = st.trade;
    if (!tr) return;
    const o = tr.offer;
    if (this.managing === decision) return;
    this.managing = null;
    if (this.openedFor === decision && this.isOpen) return;
    this.openedFor = decision;
    const mine: TradeDraft = {
      target: o.proposer, give_spaces: o.get_spaces, get_spaces: o.give_spaces,
      give_cash: o.get_cash, get_cash: o.give_cash, give_cards: o.get_cards, get_cards: o.give_cards,
    };
    const accept = actions.find(a => a.kind === "accept_trade");
    const reject = actions.find(a => a.kind === "reject_trade");
    const manage = actions.some(a => MANAGE_KINDS.has(a.kind));
    const side = (spaces: number[], cash: number, cards: number) =>
      (spaces.map(i => miniCard(board, i, true, false, !!st.mortgaged[i])).join("")
        + (cash ? `<div class="cashchip">${money(cash)}</div>` : "")
        + (cards ? `<div class="cashchip">${cards} × jail card</div>` : "")) || `<span class="muted">nothing</span>`;
    const exchanges = tr.exchanges ?? 1;
    const kind = exchanges > 1 ? "counters" : "proposes a trade";
    const history: Offer[] = tr.history ?? [];
    this.mount("respond", `
      <div class="tw-banner">↩ ${esc(names[o.proposer])} ${kind} (offer ${exchanges} of ${limits.exchanges} in this negotiation)</div>
      <div class="tw-cols">
        <div class="tw-col"><h4>You give</h4><div class="cards">${side(mine.give_spaces, mine.give_cash, mine.give_cards)}</div></div>
        <div class="tw-swap" aria-hidden="true">⇄</div>
        <div class="tw-col"><h4>You get</h4><div class="cards">${side(mine.get_spaces, mine.get_cash, mine.get_cards)}</div></div>
      </div>
      ${history.length > 1 ? `<div class="hist">${history.map((h, k) => `<div class="${k === history.length - 1 ? "now" : ""}">${esc(offerTerms(h, board, names))}</div>`).join("")}</div>` : ""}
      <div class="hints">${hintsHtml(tradeHints(t, this.me, mine))}</div>
      <div class="tw-ft"><span class="muted">The decision is yours</span>
        ${manage ? `<button type="button" class="btn tw-manage" title="Before answering you can build, sell buildings and mortgage spaces (decision panel); the offer will wait">Manage property…</button>` : ""}
        ${reject ? `<button type="button" class="btn tw-reject">Reject</button>` : ""}
        ${canCounter ? `<button type="button" class="btn gold tw-counter">Counter-offer…</button>` : ""}
        ${accept ? `<button type="button" class="btn ok tw-accept">Accept</button>` : ""}</div>`);
    const el = this.el!;
    this.bindCardTips(t);
    el.querySelector(".tw-accept")?.addEventListener("click", () => { this.close(); this.handlers.act(accept!); });
    el.querySelector(".tw-reject")?.addEventListener("click", () => { this.close(); this.handlers.act(reject!); });
    el.querySelector(".tw-counter")?.addEventListener("click", () => { onCounter(); this.compose(t, limits, decision, { ...mine }, true); });
    el.querySelector(".tw-manage")?.addEventListener("click", () => { this.close(); this.managing = decision; });
  }

  /** Back to a hidden decision window after managing property. */
  reopen() {
    this.managing = null;
    this.openedFor = "";
  }

  /** The decision about a taken-over mortgaged space: pay off at once or interest only; before that, full rights. */
  takeover(t: Table, decision: string, space: number, actions: Action[], canPropose: boolean, onTrade: () => void) {
    const { board, state: st } = t;
    if (this.managing === decision) return;
    if (this.openedFor === decision && this.isOpen) return;
    this.managing = null;
    this.openedFor = decision;
    const { interest, payoff } = mortgageCost(board, space);
    const pay = actions.find(a => a.kind === "unmortgage" && a.params?.space === space);
    const pass = actions.find(a => a.kind === "pass");
    const manage = actions.some(a => MANAGE_KINDS.has(a.kind) && !(a.kind === "unmortgage" && a.params?.space === space));
    const cash = st.players[this.me].cash;
    const missing = Math.max(0, payoff - cash);
    const hint = missing
      ? `⚠ You are ${money(missing)} short for an immediate payoff. ${manage || canPropose ? "You can raise it first: mortgage spaces or sell buildings (Manage property…) or sell a space to a player (Propose a trade…) - then you return to this decision." : "Only the interest remains."}`
      : `✓ You can afford the payoff (you have ${money(cash)}).`;
    this.mount("takeover", `
      <div class="tw-banner">🏦 Taken-over mortgaged space: ${esc(board.spaces[space].name)}</div>
      <div class="tw-cols tk-cols"><div class="tw-col"><div class="cards">${miniCard(board, space, true, false, true)}</div></div>
        <div class="tw-col tk-opts">
          <div><b>Pay off at once:</b> ${money(payoff)} (mortgage + 10%) - the space is no longer mortgaged.</div>
          <div><b>Interest only:</b> ${money(interest)} - the space stays mortgaged, redeeming it later costs 110% again.</div>
        </div></div>
      <div class="hints"><div class="${missing ? "warn" : "ok"}">${esc(hint)}</div></div>
      <div class="tw-ft"><span class="muted">You have ${money(cash)}</span>
        ${manage ? `<button type="button" class="btn tk-manage" title="Building, selling buildings, mortgages (decision panel); the decision will wait">Manage property…</button>` : ""}
        ${canPropose ? `<button type="button" class="btn gold tk-trade" title="For example selling a space to a player for cash; after the negotiation you return to this decision">Propose a trade…</button>` : ""}
        ${pass ? `<button type="button" class="btn tk-interest">Interest only (${money(interest)})</button>` : ""}
        ${pay ? `<button type="button" class="btn ok tk-pay">Pay off at once (${money(payoff)})</button>` : ""}</div>`);
    const el = this.el!;
    this.bindCardTips(t);
    el.querySelector(".tk-pay")?.addEventListener("click", () => { this.close(); this.handlers.act(pay!); });
    el.querySelector(".tk-interest")?.addEventListener("click", () => { this.close(); this.handlers.act(pass!); });
    el.querySelector(".tk-trade")?.addEventListener("click", () => onTrade());
    el.querySelector(".tk-manage")?.addEventListener("click", () => { this.close(); this.managing = decision; });
  }

  /** Hovering a small card shows the full title deed next to it (as on the board). */
  private bindCardTips(t: Table) {
    this.el!.querySelectorAll<HTMLElement>(".mc[data-space]").forEach(c => {
      c.addEventListener("mouseenter", () => this.showCardTip(c, t));
      c.addEventListener("mouseleave", () => this.hideCardTip());
    });
  }

  private showCardTip(card: HTMLElement, t: Table) {
    const i = Number(card.dataset.space);
    if (!this.tip) {
      this.tip = document.createElement("div");
      this.tip.className = "tw-tip";
      this.host.appendChild(this.tip);
    }
    const tip = this.tip;
    tip.innerHTML = ownerBadge(t.board, t.state, i, t.names, this.me) + deedHtml(t.board, t.state, i, t.names);
    tip.style.display = "block";
    const r = card.getBoundingClientRect(), box = this.host.getBoundingClientRect();
    const w = tip.offsetWidth, width = this.host.clientWidth;
    if (width < 760) {
      tip.style.left = Math.max(8, (width - w) / 2) + "px";
      tip.style.top = "8px";
      return;
    }
    const left = r.right - box.left + 10 + w > width ? r.left - box.left - w - 10 : r.right - box.left + 10;
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.max(8, Math.min(r.top - box.top - 60, this.host.clientHeight - tip.offsetHeight - 8)) + "px";
  }

  private hideCardTip() {
    this.tip?.remove();
    this.tip = null;
  }

  private mount(mode: "compose" | "respond" | "takeover", html: string) {
    this.hideCardTip();
    if (mode !== "compose") this.composedFor = null;
    this.mode = mode;
    if (!this.el) {
      this.el = document.createElement("div");
      this.el.className = "modal tw";
      this.host.appendChild(this.el);
    }
    this.el.innerHTML = `<div class="tw-win" role="dialog" aria-modal="true" aria-label="${mode === "compose" ? "Trade offer" : mode === "respond" ? "Trade offer received" : "Taken-over mortgaged space"}">${html}</div>`;
    this.el.querySelectorAll(".tw-close").forEach(b => b.addEventListener("click", () => this.close()));
  }
}

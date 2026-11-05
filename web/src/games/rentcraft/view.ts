// Rentcraft table screen: the 3D table with the original Rentcraft side panel (players, decision, camera, log), seat tags,
// space tips with the title deed, the trade window, the drawn-card show and the result. Everything reads the public API.
import { element } from "../../dom";
import type { GameViewOptions, MountedGameView } from "../../game-view";
import type { Action, ActionList, GameCommand, GameEvent, View } from "../../play-types";
import { SharedTable3D } from "../../scene/table";
import { confirmDialog, mountGameBar, rulesDialog, toast } from "../../ui/gamebar";
import { appearance, RentcraftScene } from "./scene";
import { animals, type Animal } from "./pieces";
import { presentationEvents } from "./events";
import { deedHtml, ownerBadge } from "./deed";
import { esc, formatLeft, money, mortgageCost, seatClass, seatKind, seatNames, tradeableSpaces } from "./format";
import { eventLine, logHtml, logPanelHtml, memoryEvent, windowLabel, type LogFilter, type LogLine, type MemoryEntry } from "./log";
import {
  ActionPanel, CardShow, autopilotHtml, deadlineInfoHtml, debtInfoHtml, phaseText, playersHtml, seatTagsHtml,
  takeoverInfoHtml, tradeButtonHtml, windowInfoHtml, type Table, type TradeButtonMode,
} from "./panel";
import { TradeDialog, type TradeDraft, type TradeLimits } from "./tradedialog";
import { legendHtml, lineChartSvg } from "../../ui/chart";
import type { RentBoard, RentState } from "./types";
import "./style.css";

const HINT = "🖱️ drag - rotate · wheel - zoom · hover a space or card - preview, click - pin · 1-4 player, T from above, 0 diagonal";
type Deadline = { at: number; effect: string; kind: "window" | "decision" };
type RentView = View<RentState> & { deadline?: Deadline | null; server_time?: number };

export function mountRentcraftView(root: HTMLElement, options: GameViewOptions): MountedGameView {
  const board = options.boardSpec as RentBoard;
  const host = element("section", "rc-table tc-screen");
  const canvas = element("canvas", "rc-canvas");
  host.tabIndex = -1;
  canvas.tabIndex = 0;
  canvas.setAttribute("aria-label", "Rentcraft 3D board");
  const tags = element("div", "rc-tags");
  const bar = options.gameBar ?? mountGameBar({ gameId: "rentcraft", onBack: options.onExit, backLabel: options.onExit ? "← Back" : undefined });
  const top = bar.element;
  top.classList.add("rc-top");
  const hint = element("div", "hint rc-hint", HINT);
  const autopilotBar = element("div", "autopilot-bar");
  const hud = element("aside", "hud rc-hud");
  hud.setAttribute("aria-label", "Table panel");
  const playersPanel = element("div", "panel");
  const playersTitle = element("h4", "", "Players");
  const playersBox = element("div", "pl-list");
  playersPanel.append(playersTitle, playersBox);
  const decisionPanel = element("div", "panel actions-panel");
  const decisionTitle = element("h4", "", "Game progress");
  const actionsBox = element("div", "actions");
  actionsBox.setAttribute("aria-live", "polite");
  decisionPanel.append(decisionTitle, actionsBox);
  const movePanel = element("div", "panel mind");
  const moveBox = element("div");
  const moveTitle = element("h4", "", "In the player's head");
  movePanel.append(moveTitle, moveBox);
  movePanel.hidden = true;
  const worthPanel = element("div", "panel worth");
  const worthBox = element("div");
  worthPanel.append(element("h4", "", "Net worth"), worthBox);
  worthPanel.hidden = true;
  const cameraPanel = element("div", "panel");
  const cameraButtons = element("div", "btns");
  cameraPanel.append(element("h4", "", "Camera"), cameraButtons);
  const logPanel = element("div");
  logPanel.innerHTML = logPanelHtml();
  const logLinesBox = logPanel.querySelector<HTMLElement>(".log-lines")!;
  hud.append(playersPanel, decisionPanel, movePanel, worthPanel, cameraPanel, logPanel.firstElementChild!);
  const replaySlot = element("div", "rc-replay bc-bottom");
  const tip = element("div", "tip rc-tip");
  tip.setAttribute("role", "tooltip");
  const overlay = element("div", "rc-overlay");
  const notice = element("p", "rc-notice scene-notice");
  notice.hidden = true;
  host.append(canvas, tags, top, hint, autopilotBar, hud, replaySlot, tip, overlay, notice);
  root.append(host);
  const fullscreenRoot = options.fullscreenRoot ?? host;

  let table: SharedTable3D | undefined, scene: RentcraftScene | undefined, observer: ResizeObserver | undefined;
  let dto: RentView | undefined, list: ActionList = { decision_id: null, actions: [] }, disposed = false;
  let cursor: number | null = null, lastDecision = "", lastPlayers = "", history: LogLine[] = [];
  let names: string[] = [], kinds: string[] = [];
  let logFilter: LogFilter = "all";
  let resultShown = false;
  let deadlineEnd: number | null = null, deadlineKind: "window" | "decision" | null = null;
  const panel = new ActionPanel(actionsBox, id => {
    const current = list.decision_id;
    if (canPlay() && current && list.actions.some(a => a.id === id)) options.onAction?.({ decisionId: current, actionId: id });
  });
  const cardShow = new CardShow(host);
  const seat = () => dto?.observation?.viewer ?? null;
  const state = () => dto!.observation!.data;
  const setCamera = (name: string) => table?.setCamera(name, 900);
  const failure = (text: string) => { notice.hidden = false; notice.textContent = text; };
  const tradeDlg = new TradeDialog(host, 0, {
    send: (draft, counter) => sendTrade(draft, counter),
    act: action => {
      const current = list.decision_id;
      if (canPlay() && current && list.actions.some(a => a.id === action.id)) options.onAction?.({ decisionId: current, actionId: action.id });
    },
  });

  let pieces: Animal[] = [];
  function tableInfo(): Table {
    return { board, state: state(), names, kinds, me: seat(), animals: pieces };
  }
  /** The pieces chosen at the table (seat tokens) replace the default animals on the board and in the panels. */
  function applyPieces(view: View<RentState>) {
    const next = names.map((_, i) => {
      const token = (view.seats?.find(x => x.seat === i) as { token?: string } | undefined)?.token;
      return (animals as readonly string[]).includes(token ?? "") ? token as Animal : animals[i % animals.length];
    });
    next.forEach((a, i) => { if (pieces[i] !== a && (pieces[i] || a !== animals[i % animals.length])) scene?.setAnimal(i, a); });
    pieces = next;
  }
  function canPlay() {
    return !disposed && !options.readOnly && !!dto && dto.status !== "replay" && dto.status !== "completed" && dto.observation?.viewer != null;
  }
  const commandSpec = (kind: string) => list.commands?.find(c => c.kind === kind);
  function command(spec: GameCommand, expected: number, current: string | null, params: Record<string, unknown> = {}) {
    if (!canPlay() || !options.onCommand || list.expected_revision !== expected || list.decision_id !== current || !list.commands?.some(c => c.kind === spec.kind))
      return Promise.reject(Error("That choice has changed."));
    return options.onCommand({ kind: spec.kind, params, expectedRevision: expected, ...(spec.scope === "decision" && current ? { decisionId: current } : {}) });
  }
  function runCommand(kind: string, params: Record<string, unknown> = {}) {
    const spec = commandSpec(kind);
    if (!spec || list.expected_revision == null) return Promise.reject(Error("That choice has changed."));
    return command(spec, list.expected_revision, list.decision_id, params);
  }
  function limits(): TradeLimits {
    const s = state(), schema = options.settings?.table?.properties ?? {};
    const value = (key: string, fallback: number) => {
      const v = (s.settings as Record<string, unknown> | undefined)?.[key] ?? dto?.config?.settings?.[key] ?? schema[key]?.default;
      return typeof v === "number" ? v : fallback;
    };
    return { perTurn: value("max_trades_per_turn", 3), exchanges: value("max_trade_exchanges", 4) };
  }
  function sendTrade(draft: TradeDraft, counter: boolean) {
    const kind = counter ? "counter_trade" : "propose_trade";
    const decision = tradeDlg.composedFor;
    if (!canPlay() || list.decision_id !== decision) return Promise.reject(Error("That choice has changed."));
    return runCommand(kind, { ...draft });
  }

  /** Opens a new offer; in a timed offer window the seat first claims the window so that its countdown stops. */
  async function startTrade(prefill?: Partial<TradeDraft>) {
    if (!canPlay()) return;
    const revision = list.expected_revision, id = list.decision_id;
    const claim = commandSpec("request_trade");
    if (claim && commandSpec("propose_trade") && deadlineKind === "window" && revision != null) {
      const serial = state().window?.serial;
      try {
        await command(claim, revision, id);
      } catch (e) {
        if (!disposed) failure((e as Error).message);
        return;
      }
      if (!canPlay() || state().phase !== "trade_window" || state().window?.serial !== serial || !commandSpec("propose_trade")) return;
    }
    if (!commandSpec("propose_trade")) return;
    tradeDlg.me = seat()!;
    tradeDlg.compose(tableInfo(), limits(), list.decision_id, prefill);
  }
  function tradeMode(): TradeButtonMode {
    const me = seat();
    if (!canPlay() || me == null || state().players[me]?.bankrupt || !options.onCommand || list.expected_revision == null) return null;
    if (commandSpec("propose_trade")) return "compose";
    if ((state().trade_requests as number[] | undefined)?.includes(me)) return "pending";
    if (commandSpec("request_trade")) return "request";
    return null;
  }

  // ---------- the 3D table ----------
  let appliedInsets: { top: number; right: number; bottom: number } | undefined;
  let tipSpace: number | null = null, pinned = false, overTip = false, hoverSpace: number | null = null;
  let tipTimer: ReturnType<typeof setTimeout> | undefined;
  let lastXY: [number, number] = [-1, -1];
  try {
    table = new SharedTable3D(canvas, host, failure, appearance);
    scene = new RentcraftScene(board, (i, x, y) => onHover(i, x, y), (i, x, y) => onPick(i, x, y), anchors);
    scene.mount(table);
    observer = new ResizeObserver(measure);
    for (const el of [host, hud, top, hint, replaySlot]) observer.observe(el);
    measure();
  } catch (error) {
    console.warn("The Rentcraft 3D table could not start.", error);
    scene?.dispose();
    scene = undefined;
    table?.dispose();
    table = undefined;
    failure("WebGL is unavailable. Use the table panel to follow the game and make decisions.");
    tags.hidden = true; // seat tags follow the 3D camera, so without it they would pile up in a corner
  }

  /** The player tags at the board edges follow the camera; they stay in the visible part (not under the panel). */
  function anchors() {
    if (!scene) return;
    const narrow = host.clientWidth < 760;
    const right = !narrow && hud.offsetWidth ? host.clientWidth - hud.offsetWidth - 16 : host.clientWidth;
    const bottom = narrow && hud.offsetHeight ? host.clientHeight - hud.offsetHeight - 8 : host.clientHeight;
    tags.querySelectorAll<HTMLElement>("[data-tag]").forEach(t => {
      const p = scene!.seatAnchor(Number(t.dataset.tag));
      t.style.visibility = p ? "visible" : "hidden";
      if (!p) return;
      const hw = t.offsetWidth / 2 + 4, hh = t.offsetHeight / 2 + 4;
      const x = Math.min(Math.max(p.x, hw), Math.max(hw, right - hw));
      const y = Math.min(Math.max(p.y, hh), Math.max(hh, bottom - hh));
      t.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, -50%)`;
    });
  }
  function measure() {
    if (disposed || !table) return;
    const bounds = host.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const small = host.clientWidth < 760;
    host.classList.toggle("rc-narrow", small);
    const toolbar = top.getBoundingClientRect(), side = hud.getBoundingClientRect();
    const lower = small ? side : (replaySlot.childElementCount ? replaySlot : hint).getBoundingClientRect();
    const gap = 4;
    const insets = {
      top: toolbar.height ? Math.ceil(toolbar.bottom - bounds.top + gap) : 0,
      right: !small && side.width ? Math.ceil(bounds.right - side.left + gap) : 0,
      bottom: lower.height ? Math.ceil(bounds.bottom - lower.top + gap) : 0,
    };
    if (!appliedInsets || appliedInsets.top !== insets.top || appliedInsets.right !== insets.right || appliedInsets.bottom !== insets.bottom) {
      appliedInsets = insets;
      table.setInsets({ left: 0, ...insets });
    }
    anchors();
  }

  // ---------- game bar, camera, log filter, keys ----------
  bar.setRules(() => {
    const settings: [string, string][] = [];
    if (options.settings && dto)
      for (const [schema, values] of [[options.settings.table, dto.config?.settings], [options.settings.live, dto.live]] as const)
        for (const [key, field] of Object.entries(schema?.properties ?? {})) {
          const value = values && key in values ? values[key] : field.default;
          settings.push([field.title ?? key, value === null ? "Unlimited" : String(value)]);
        }
    rulesDialog(host, "Rentcraft", options.rulesText, settings);
  });
  const camButton = (label: string, fn: () => void) => {
    const b = element("button", "btn sm", label);
    b.type = "button";
    b.onclick = fn;
    cameraButtons.append(b);
    return b;
  };
  camButton("🔭 Whole board from above", () => setCamera("top"));
  const myView = camButton("🪑 View", () => setCamera("seat" + (seat() ?? 0)));
  camButton("🎥 Diagonal", () => setCamera("free"));
  hud.querySelectorAll<HTMLButtonElement>("[data-log]").forEach(b => b.addEventListener("click", () => {
    logFilter = b.dataset.log as LogFilter;
    hud.querySelectorAll<HTMLElement>("[data-log]").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    renderLog();
  }));
  const pickSeat = (e: Event) => {
    const s = (e.target as HTMLElement).closest<HTMLElement>("[data-seat]")?.dataset.seat;
    if (s != null) setCamera("seat" + Number(s));
  };
  playersBox.addEventListener("click", pickSeat);
  playersBox.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickSeat(e); } });
  const modalOpen = () => !!host.querySelector(".modal:not(.soft)");
  const onKey = (e: KeyboardEvent) => {
    if (disposed || !host.isConnected) return;
    const target = e.target as HTMLElement;
    if (target !== document.body && !host.contains(target)) return;
    if (e.key === "Escape") {
      if (pinned) unpin();
      return;
    }
    if (target.closest("input,select,textarea,[contenteditable]") || modalOpen() || e.ctrlKey || e.metaKey || e.altKey) return;
    if (/^[1-4]$/.test(e.key)) setCamera("seat" + (Number(e.key) - 1));
    else if (e.key.toLowerCase() === "t") setCamera("top");
    else if (e.key === "0") setCamera("free");
  };
  window.addEventListener("keydown", onKey);

  // ---------- decision panel clicks ----------
  actionsBox.addEventListener("click", e => {
    const el = e.target as HTMLElement;
    if (!dto?.observation || !canPlay()) return;
    if (el.closest(".show-offer")) { tradeDlg.reopen(); openDecisionWindows(true); return; }
    if (el.closest(".show-takeover")) { tradeDlg.reopen(); openDecisionWindows(true); return; }
    if (el.closest(".liquidate-btn")) {
      confirmDialog(host, "Full bank liquidation?",
        "The game mortgages your spaces and sells buildings to the bank at half price (evenly) until the debt is paid or the bank has nothing more to give. The buildings disappear.",
        "🏦 Yes, raise it from the bank", () => void runCommand("liquidate").catch(err => failure(err.message)));
      return;
    }
    const cmd = el.closest<HTMLElement>("[data-cmd]")?.dataset.cmd;
    if (cmd) { void runCommand(cmd).catch(err => failure(err.message)); return; }
    const trade = el.closest<HTMLElement>(".trade-btn");
    if (!trade) return;
    if (trade.dataset.mode === "request") {
      void runCommand("request_trade").then(() => toast(host, "Requested - wait for the nearest moment.")).catch(err => failure(err.message));
      return;
    }
    if (trade.dataset.mode === "compose") void startTrade();
  });
  autopilotBar.addEventListener("click", e => {
    if ((e.target as HTMLElement).closest(".reclaim-btn")) void runCommand("reclaim").catch(err => failure(err.message));
  });

  // ---------- space tip ----------
  type TipAction = { kind: "act"; action: Action; label: string } | { kind: "trade"; target: number; space: number; label: string };
  function spaceActions(space: number): TipAction[] {
    const me = seat();
    if (me == null || !canPlay() || state().players[me]?.bankrupt) return [];
    const owner = state().owner[space];
    if (owner == null) return [];
    if (owner === me) {
      if (!list.decision_id) return [];
      const out: TipAction[] = [];
      for (const a of list.actions) {
        if (a.params?.space !== space) continue;
        if (a.kind === "mortgage") out.push({ kind: "act", action: a, label: `🏦 Mortgage (+${money(mortgageCost(board, space).principal)})` });
        if (a.kind === "unmortgage") out.push({ kind: "act", action: a, label: `💰 Redeem (${money(mortgageCost(board, space).payoff)})` });
      }
      return out;
    }
    if (commandSpec("propose_trade") && !state().players[owner]?.bankrupt && tradeableSpaces(board, state(), owner).includes(space))
      return [{ kind: "trade", target: owner, space, label: "🤝 Propose a trade" }];
    return [];
  }
  function tradeNote(space: number): string {
    const me = seat();
    if (me == null || !canPlay()) return "";
    const owner = state().owner[space];
    if (owner == null || owner === me) return "";
    if (!commandSpec("propose_trade"))
      return "You can propose a trade at the moment of your own decision (turn, purchase, auction, debt payment, offer window) - outside it, ask for a trade with the “Propose a trade” button.";
    if (!tradeableSpaces(board, state(), owner).includes(space)) return "There are buildings in this colour set - this space cannot be traded now.";
    return "";
  }
  tip.addEventListener("mouseenter", () => (overTip = true));
  tip.addEventListener("mouseleave", () => { overTip = false; if (!pinned) scheduleTip(); });
  tip.addEventListener("click", e => {
    const t = e.target as HTMLElement;
    if (t.closest(".tip-x")) return unpin();
    const k = t.closest<HTMLElement>("[data-tip-act]")?.dataset.tipAct;
    if (k == null || tipSpace == null) return;
    const a = spaceActions(tipSpace)[Number(k)];
    if (!a) return;
    unpin();
    if (a.kind === "act") {
      const current = list.decision_id;
      if (current) options.onAction?.({ decisionId: current, actionId: a.action.id });
    } else void startTrade({ target: a.target, get_spaces: [a.space] });
  });
  function onHover(space: number | null, x: number, y: number) {
    hoverSpace = space;
    lastXY = [x, y];
    if (pinned || overTip) return;
    if (tip.classList.contains("interactive") && space !== tipSpace) return scheduleTip();
    clearTimeout(tipTimer);
    showTip(space, x, y);
  }
  function scheduleTip() {
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => { if (!overTip && !pinned) showTip(hoverSpace, ...lastXY, true); }, 450);
  }
  function onPick(space: number | null, x: number, y: number) {
    if (space == null) return unpin();
    clearTimeout(tipTimer);
    pinned = true;
    showTip(space, x, y, true);
  }
  function unpin() {
    pinned = false;
    overTip = false;
    showTip(hoverSpace, ...lastXY, true);
  }
  function showTip(space: number | null, x: number, y: number, place = false) {
    if (space == null || !dto?.observation) {
      tip.style.display = "none";
      tip.classList.remove("interactive", "pinned");
      tipSpace = null;
      scene?.highlight(null);
      return;
    }
    const moved = space !== tipSpace;
    tipSpace = space;
    const acts = spaceActions(space);
    const note = acts.length ? "" : tradeNote(space);
    tip.innerHTML = (pinned ? `<button type="button" class="tip-x" title="Close (Esc)" aria-label="Close">✕</button>` : "")
      + ownerBadge(board, state(), space, names, seat()) + deedHtml(board, state(), space, names)
      + (acts.length ? `<div class="tip-acts">${acts.map((a, k) => `<button type="button" class="btn sm ${a.kind === "trade" ? "gold" : ""}" data-tip-act="${k}">${esc(a.label)}</button>`).join("")}</div>` : "")
      + (note ? `<div class="tip-note">${esc(note)}</div>` : "");
    const interactive = pinned || acts.length > 0;
    tip.classList.toggle("interactive", interactive);
    tip.classList.toggle("pinned", pinned);
    tip.style.display = "block";
    if (x < 0 || (!moved && interactive && !place)) return;
    const box = host.getBoundingClientRect(), width = host.clientWidth, height = host.clientHeight;
    if (width < 760) {
      tip.style.left = Math.max(8, (width - tip.offsetWidth) / 2) + "px";
      tip.style.top = "8px";
      return;
    }
    const lx = x - box.left, ly = y - box.top, reserve = hud.offsetWidth ? hud.offsetWidth + 20 : 0;
    tip.style.left = Math.max(8, lx + 20 + tip.offsetWidth > width - reserve ? lx - tip.offsetWidth - 20 : lx + 20) + "px";
    tip.style.top = Math.max(8, Math.min(ly - 150, height - tip.offsetHeight - 8)) + "px";
  }

  // ---------- countdowns ----------
  const ticker = setInterval(tick, 250);
  function tick() {
    if (deadlineEnd == null) return;
    const left = (deadlineEnd - performance.now()) / 1000;
    host.querySelectorAll<HTMLElement>(".deadline-info").forEach(el => el.classList.toggle("urgent", left <= 30));
    host.querySelectorAll<HTMLElement>("[data-deadline-left]").forEach(el => (el.textContent = formatLeft(left)));
    host.querySelectorAll<HTMLElement>(".window-info [data-left]").forEach(el => (el.textContent = String(Math.max(0, Math.ceil(left)))));
  }

  // ---------- rendering ----------
  function renderLog() {
    logLinesBox.innerHTML = logHtml(history, logFilter);
  }
  function floatCash(player: number, delta: number) {
    const target = playersBox.querySelector(`[data-cash="${player}"]`);
    if (!target || !delta) return;
    const r = target.getBoundingClientRect(), box = host.getBoundingClientRect();
    const f = element("div", "float " + (delta > 0 ? "plus" : "minus"), (delta > 0 ? "+" : "−") + money(Math.abs(delta)));
    f.style.left = r.left - box.left - 10 + "px";
    f.style.top = r.top - box.top - 4 + "px";
    host.append(f);
    setTimeout(() => f.remove(), 1600);
  }
  /** The trade answer or the taken-over space decision opens by itself once per decision. */
  function openDecisionWindows(force = false) {
    const s = state(), me = seat(), id = list.decision_id;
    if (!canPlay() || me == null || !id || dto?.observation?.decision?.id !== id) return;
    tradeDlg.me = me;
    if (s.phase === "trade_response" && s.trade?.offer.target === me && list.actions.some(a => a.kind === "accept_trade" || a.kind === "reject_trade"))
      tradeDlg.respond(tableInfo(), limits(), id, list.actions, !!commandSpec("counter_trade"), () => undefined);
    else if (s.phase === "mortgage_takeover" && s.pending_takeovers?.[0]?.[0] === me)
      tradeDlg.takeover(tableInfo(), id, s.pending_takeovers[0][1], list.actions, !!commandSpec("propose_trade"), () => void startTrade());
    if (force && !tradeDlg.isOpen) openDecisionWindows();
  }
  function renderDecision(view: RentView) {
    const s = state(), me = seat(), t = tableInfo();
    const id = list.decision_id;
    const mine = canPlay() && !!id && view.observation?.decision?.id === id;
    decisionTitle.textContent = s.result ? "Final standings" : canPlay() ? "Your move" : "Game progress";
    const pending = s.pending_takeovers?.[0];
    const takeoverSpace = mine && s.phase === "mortgage_takeover" && pending?.[0] === me ? pending[1] : null;
    let extra = "";
    if (mine && (s.phase === "buy_decision" || s.phase === "auction")) {
      const sp = s.phase === "auction" ? s.auction?.space : s.pending_buy ?? s.players[me!].position;
      if (sp != null) extra += `<div class="inline-deed">${deedHtml(board, s, sp, names)}</div>`;
    }
    if (s.trade?.offer) {
      const show = mine && s.phase === "trade_response" ? ` <button type="button" class="btn sm show-offer">Show the offer</button>` : "";
      const o = s.trade.offer;
      const n = s.trade.exchanges ?? 1;
      extra += `<div class="trade-info">🤝 ${esc(names[o.proposer])} ${n > 1 ? "counters" : "proposes a trade to"} ${esc(names[o.target])} (offer ${n} of ${limits().exchanges})${show}</div>`;
    }
    if (takeoverSpace != null) extra += takeoverInfoHtml(t, takeoverSpace, true);
    if (s.result) {
      const order = s.result.placements.map((place, i) => [place, i]).sort((a, b) => a[0] - b[0]);
      extra += `<table class="rank">${order.map(([place, i]) => `<tr><td>${place}.</td><td><span class="d pcbg ${seatClass(i)}"></span> ${esc(names[i])}</td><td>${money(s.result!.scores[i])}</td></tr>`).join("")}</table>`;
    }
    const control = view.control;
    const timedDecision = mine && deadlineKind === "decision";
    extra = deadlineInfoHtml(timedDecision, (control?.deadline ?? view.deadline)?.effect) + debtInfoHtml(t, mine && !!commandSpec("liquidate"), list.context?.debt) + extra
      + windowInfoHtml(t, mine && deadlineKind === "window") + tradeButtonHtml(tradeMode());
    const actions = mine && options.onAction ? list.actions.filter(a => !list.commands?.some(c => c.kind === a.kind)) : [];
    const handled = new Set(["propose_trade", "counter_trade", "request_trade", "resign", "liquidate", "reclaim"]);
    const others = canPlay() && options.onCommand && list.expected_revision != null
      ? (list.commands ?? []).filter(c => !handled.has(c.kind) && (c.scope !== "decision" || mine)) : [];
    if (others.length) extra += `<div class="btns">${others.map(c => `<button type="button" class="btn" data-cmd="${esc(c.kind)}">${esc(c.label)}</button>`).join("")}</div>`;
    const label = (a: Action) => {
      if (takeoverSpace != null && a.kind === "pass") return `Interest only (${money(mortgageCost(board, takeoverSpace).interest)})`;
      if (takeoverSpace != null && a.kind === "unmortgage" && a.params?.space === takeoverSpace) return `Pay off at once (${money(mortgageCost(board, takeoverSpace).payoff)})`;
      return a.label;
    };
    panel.render(actions, phaseText(t), mine, extra, takeoverSpace, label);
    tick();
    const ap = autopilotHtml(!!control?.autopilot, !!commandSpec("reclaim"), control?.autopilot_round);
    if (autopilotBar.innerHTML !== ap) autopilotBar.innerHTML = ap;
  }
  function renderResult(view: RentView) {
    const s = state();
    if (view.status !== "completed" || !s.result || resultShown) return;
    resultShown = true;
    const r = s.result;
    const winner = r.placements.indexOf(1);
    const details = (r as unknown as { details?: { rounds?: number } }).details;
    const rounds = details?.rounds ?? s.round;
    const order = r.placements.map((place, i) => [place, i]).sort((a, b) => a[0] - b[0]);
    const rows = order.map(([place, i]) => `<tr><td>${place}.</td><td><span class="d pcbg ${seatClass(i)}"></span> ${esc(names[i])}</td><td>${money(r.scores[i])}</td></tr>`).join("");
    const modal = element("div", "modal soft");
    modal.innerHTML = `<div class="modal-box result" role="dialog" aria-label="Game over"><h3>Game over</h3>
      <p><b>${esc(names[winner] ?? "?")}</b> wins${r.reason === "round_limit" ? " (round limit, by net worth)" : ""} after ${rounds} round${rounds === 1 ? "" : "s"}.</p>
      <table class="rank">${rows}</table><div class="btns end"><button type="button" class="btn" data-stay>Stay at the table</button>
      ${options.onExit ? `<button type="button" class="btn pri" data-back>Back to Play</button>` : ""}</div></div>`;
    modal.querySelector("[data-stay]")!.addEventListener("click", () => modal.remove());
    modal.querySelector("[data-back]")?.addEventListener("click", () => options.onExit?.());
    overlay.replaceChildren(modal);
  }
  function renderMove(view: RentView, events: GameEvent[]) {
    const replay = view.status === "replay";
    movePanel.hidden = !replay;
    renderWorth(view);
    host.classList.toggle("rc-replaying", replay);
    if (!replay) return;
    const framing = (e: GameEvent): LogLine | null => {
      const p = e.payload ?? {};
      if (e.kind === "trade_window") return { text: `Offer window opens (${windowLabel(p.window)})` };
      if (e.kind === "trade_window_end") return { text: "Offer window closes" };
      if (e.kind === "start_player") return { text: `${names[p.player] ?? "?"} starts the game` };
      if (e.kind === "game_over") return { text: "The game is over" };
      return null;
    };
    const lines = events.map(e => eventLine(e, board, names) ?? framing(e)).filter((l): l is LogLine => !!l);
    const s = state();
    // The actor is shown only when one player made every listed move (the opening rolls belong to everybody).
    const players = new Set(events.filter(e => e.payload?.player != null && e.kind !== "trade_window" && e.kind !== "trade_window_end").map(e => e.payload.player as number));
    const actor = players.size === 1 ? [...players][0] : undefined;
    let html = actor != null ? `<div class="who"><span class="dot pcbg ${seatClass(actor)}"></span> <b>${esc(names[actor])}</b> <span class="muted">(${esc(kinds[actor] || "player")})</span></div>` : "";
    html += lines.length ? `<div class="moves">${lines.map(l => `<div class="${l.trade ? "trade" : ""}">${esc(l.text)}</div>`).join("")}</div>`
      : `<div class="muted">${s.round <= 1 && s.roll_count === 0 ? "Start of the game." : "No visible change at this position."}</div>`;
    if (s.result) html += `<div class="chosen">🏁 ${esc(names[s.result.placements.indexOf(1)] ?? "?")} wins.</div>`;
    // With the replay timeline the panel shows the decision itself, as in the shared design: who, the chosen move and
    // the model's action probabilities (or the agent's explanation). The value estimate is recorded but not shown.
    const timeline = (view as { replay_timeline?: { positions: { decision?: { player: number; action: string; explanation?: string; meta?: { probabilities?: { action: string; probability: number }[] } } | null }[] } }).replay_timeline;
    const at = (view as { replay_position?: number }).replay_position;
    const note = timeline && at != null ? timeline.positions[at]?.decision : undefined;
    if (timeline) {
      moveTitle.textContent = "In the player's head";
      if (!note) html = `<div class="muted">${at === 0 ? "Start of the game." : "No decision at this position (an automatic step)."}</div>`;
      else {
        html = `<div class="who"><span class="dot pcbg ${seatClass(note.player)}"></span> <b>${esc(names[note.player])}</b> <span class="muted">(${esc(kinds[note.player] || "player")})</span></div>
          <div class="chosen">➜ ${esc(note.action)}</div>`;
        const probs = note.meta?.probabilities;
        const explanation = note.explanation && !/^Selected by the greedy observation policy\.?$/.test(note.explanation) ? note.explanation : "";
        if (probs?.length) html += `<div class="probs">${probs.map(p => `<div class="prob ${p.action === note.action ? "on" : ""}"><span>${esc(p.action)}</span><i style="width:${(p.probability * 100).toFixed(1)}%"></i><b>${(p.probability * 100).toFixed(0)}%</b></div>`).join("")}</div>`;
        else if (explanation) html += `<div class="expl">“${esc(explanation)}”</div>`;
        else html += `<div class="muted">No recorded reasoning.</div>`;
      }
    } else moveTitle.textContent = "This move";
    moveBox.innerHTML = html;
  }

  let worthKey = "";
  /** The net worth of every seat over the whole replay, with the shown position marked (needs the replay timeline). */
  function renderWorth(view: RentView) {
    const timeline = (view as { replay_timeline?: { positions: { metrics: { net_worth?: number[] } | null }[] } }).replay_timeline;
    const at = (view as { replay_position?: number }).replay_position;
    worthPanel.hidden = view.status !== "replay" || !timeline;
    if (worthPanel.hidden || !timeline) return;
    const key = JSON.stringify([timeline.positions.length, at, names]);
    if (key === worthKey) return;
    worthKey = key;
    const rows = timeline.positions.map(p => p.metrics?.net_worth ?? []);
    const seats = Math.max(0, ...rows.map(r => r.length));
    const series = Array.from({ length: seats }, (_, seat) => ({
      name: esc(names[seat] ?? `Player ${seat + 1}`), tone: `seat-${seat % 4}`,
      points: rows.flatMap((r, x) => (r[seat] == null ? [] : [[x, r[seat]] as [number, number]])),
    }));
    const top = Math.max(2000, ...rows.flat());
    worthBox.innerHTML = lineChartSvg(series, { yMin: 0, yMax: top, width: 300, height: 120, cursorX: at ?? null, xMax: Math.max(1, rows.length - 1),
      yFormat: v => `${Math.round(v / 100) / 10}k` }) + legendHtml(series);
  }
  let lobbyKey = "";
  /** A table that has not started: the real board plus a neutral seat roster. No state is invented. */
  function lobby(view: View<RentState>) {
    const seats = view.seats ?? [];
    const key = JSON.stringify([view.status, view.config.players, seats]);
    if (key === lobbyKey) return;
    lobbyKey = key;
    lastPlayers = "";
    const total = Math.max(view.config.players, seats.length);
    names = seatNames(seats, total);
    const ready = seats.filter(s => s.occupied && s.availability?.status !== "unavailable").length;
    if (!options.gameBar) bar.setTitle("Table " + view.table_id.slice(0, 8));
    playersTitle.textContent = `Seats · ${ready} of ${total} ready`;
    decisionTitle.textContent = "Waiting for players";
    panel.render([], "The game starts once every seat is ready.", false);
    const rows: string[] = [], tagRows: string[] = [];
    for (let i = 0; i < total; i++) {
      const s = seats.find(x => x.seat === i);
      const status = !s ? "Open seat" : s.kind === "human" ? (s.occupied ? "Ready" : "Open seat") : s.kind === "agent" ? (s.occupied ? "Agent joined" : "Waiting for an agent")
        : (s.display_name ?? "Bot") + (s.availability?.status === "unavailable" ? " · unavailable" : " · ready");
      // A person's or agent's own name (set by the table creator or by the agent joining), else the seat number.
      const custom = s && (s.kind === "human" || s.kind === "agent") && s.display_name && s.display_name !== "Human" && s.display_name !== "Agent" ? s.display_name : "";
      const name = esc(custom || `Player ${i + 1}`);
      rows.push(`<div class="pl" data-seat="${i}" role="button" tabindex="0"><span class="d pcbg ${seatClass(i)}"></span><span class="n"><b>${name}</b></span><span class="m"><small>${esc(status)}</small></span><span class="cam" aria-hidden="true">🎥</span></div>`);
      tagRows.push(`<div class="seat-tag ${seatClass(i)}" data-tag="${i}"><span class="nm">${name}</span><b class="cash">${esc(status)}</b></div>`);
    }
    playersBox.innerHTML = rows.join("");
    tags.innerHTML = tagRows.join("");
    anchors();
  }

  return {
    update(view: RentView, events: GameEvent[] = [], actions: ActionList = { decision_id: null, actions: [] }) {
      if (disposed) return;
      if (!view.observation) {
        lobby(view);
        return;
      }
      lobbyKey = "";
      // Fields a position may leave out are read as empty, so a partial observation never breaks the panel.
      const data = view.observation.data;
      view = { ...view, observation: { ...view.observation, data: { ...data, players: data.players ?? [], owner: data.owner ?? [], buildings: data.buildings ?? [], mortgaged: data.mortgaged ?? [], debts: data.debts ?? [] } } };
      const observation = view.observation!;
      const previous = dto?.observation ? dto.observation.data : null;
      const newPerspective = dto?.observation?.viewer !== observation.viewer || dto?.table_id !== view.table_id;
      const next = view.event_cursor ?? observation.data.event_cursor;
      const newHistory = newPerspective || dto?.status !== view.status || cursor !== next;
      const refreshPosition = newHistory || dto?.observation?.revision !== observation.revision;
      const reviewing = view.status === "replay" || view.status === "completed";
      if (newPerspective) cursor = null;
      dto = view;
      list = actions;
      const s = state(), id = actions.decision_id ?? "";
      names = seatNames(view.seats, Math.max(s.players.length, view.config?.players ?? 0));
      kinds = names.map((_, i) => seatKind(view.seats, i));
      applyPieces(view);
      if (!options.gameBar) bar.setTitle("Table " + (view.table_id ?? "").slice(0, 8) + (observation.viewer == null ? " · watching" : ""));
      myView.textContent = observation.viewer == null ? "🪑 View" : "🪑 My view";
      // Deadline: the server's own clock is the reference when it is given.
      const d = (view.control?.deadline ?? view.deadline) as Deadline | null | undefined;
      deadlineKind = d && canPlay() ? d.kind : null;
      deadlineEnd = d && canPlay() ? performance.now() + (d.at - (view.server_time ?? Date.now() / 1000)) * 1000 : null;
      // Windows of a decision that is gone are closed; a compose window lives only on its own decision.
      if (id !== lastDecision || !canPlay()) {
        if (tradeDlg.isOpen && (!tradeDlg.isCompose || tradeDlg.composedFor !== actions.decision_id)) tradeDlg.close();
        lastDecision = id;
      }
      if (tradeDlg.isCompose && (tradeDlg.composedFor !== actions.decision_id || !(actions.commands ?? []).some(c => c.kind === "propose_trade" || c.kind === "counter_trade")))
        tradeDlg.close();
      const receipts = presentationEvents(cursor, next, events, reviewing);
      if (newHistory || (reviewing && refreshPosition)) {
        const memory = (s.memory?.log ?? []) as unknown as MemoryEntry[];
        const fromMemory = () => memory.slice(-120).map(e => eventLine(memoryEvent(e), board, names)).filter((l): l is LogLine => !!l);
        if (view.status === "replay") history = fromMemory();
        else if (receipts.length) history = [...history, ...receipts.map(e => eventLine(e, board, names)).filter((l): l is LogLine => !!l)].slice(-160);
        else history = fromMemory();
        renderLog();
        if (receipts.length && !reviewing) {
          const drawn = receipts.filter(e => e.kind === "card").map(e => ({
            deck: e.payload.deck as string, player: e.payload.player as number,
            text: board.cards?.[e.payload.deck]?.find(c => c.id === e.payload.card)?.text ?? (e.payload.text as string) ?? "",
          }));
          if (drawn.length) cardShow.push(drawn, names);
        }
      }
      if (cursor === null && observation.viewer != null) table?.setCamera("seat" + observation.viewer);
      cursor = next;
      scene?.update(dto, events);
      const playerKey = JSON.stringify([s.round, s.current, s.players, view.seats, view.status, s.owner, s.mortgaged, s.buildings, view.control?.autopilot]);
      if (playerKey !== lastPlayers) {
        lastPlayers = playerKey;
        playersTitle.textContent = `Players · round ${Math.min(s.round, (s.result as { details?: { rounds?: number } } | undefined)?.details?.rounds ?? s.round)}`;
        const t = tableInfo();
        playersBox.innerHTML = playersHtml(t, view.control?.autopilot ? seat() : null);
        tags.innerHTML = seatTagsHtml(t);
        anchors();
        if (previous && receipts.length && !reviewing) s.players.forEach((p, i) => floatCash(i, p.cash - (previous.players[i]?.cash ?? p.cash)));
      }
      renderDecision(view);
      renderMove(view, events);
      measure();
      const resign = commandSpec("resign");
      bar.setSurrender(canPlay() && resign && options.onCommand && actions.expected_revision != null ? () => confirmDialog(host, "Surrender the game?",
        "You leave the game. The bank pays your debts first (mortgages, building sales) - and if that is not enough, you go bankrupt to your creditor; then your spaces return to the bank. This cannot be undone.",
        "🏳 Yes, I surrender", () => void runCommand("resign").catch(e => failure(e.message))) : null);
      if (!reviewing) openDecisionWindows();
      renderResult(view);
      if (tipSpace != null && refreshPosition) showTip(tipSpace, -1, -1);
    },
    dispose() {
      disposed = true;
      clearInterval(ticker);
      clearTimeout(tipTimer);
      window.removeEventListener("keydown", onKey);
      cardShow.clear();
      tradeDlg.close();
      observer?.disconnect();
      scene?.dispose();
      table?.dispose();
      host.remove();
    },
  };
}

import { button, element } from "./dom";
import { gameIcon } from "./ui/gameicon";
import { bindPager, pagerHtml, PAGE_SIZE } from "./ui/pager";
import { gameMark, openRules } from "./ui/rules";
import type { NavigationOptions } from "./navigation";
import { agentPrompt, mountAgentHelp } from "./agent-help";
import { renderNavigation, type Page } from "./navigation";
import { selection, settingsFields } from "./settings";
import type { BotDescriptor, GameInfo, Settings, TableList, TableSummary, View } from "./play-types";

/** What this browser remembers about one table. The credentials never leave storage, memory and request headers. */
export interface Session {
  table: string;
  credential?: string;
  creator?: string;
  seat?: number;
  gameId?: string;
  /** The creator deliberately asked to start as soon as every seat is ready. */
  autostart?: boolean;
  /** A start request was sent and its outcome is not known yet. It is never repeated automatically. */
  start?: "pending";
}
export type Intent = "join" | "watch" | "review";
const TABLE_ID = /^[a-f0-9]{32}$/;
const STORE_KEY = "boardcraft.sessions.v1";
const LEGACY_KEY = "boardcraft.recent";
const TAB_KEY = "boardcraft.session";
const MAX_KNOWN = 20;

export const isTableId = (value: unknown): value is string => typeof value === "string" && TABLE_ID.test(value);
export function originOf(base: string) {
  try {
    return new URL(base || "/", location.href).origin;
  } catch {
    return location.origin;
  }
}
/** Keeps only the fields this application owns, so stored junk or foreign keys never reach a request. */
export function cleanSession(raw: unknown): Session | null {
  const r = raw as Record<string, unknown> | null;
  if (!r || typeof r !== "object" || !isTableId(r.table)) return null;
  const s: Session = { table: r.table };
  if (typeof r.credential === "string") s.credential = r.credential;
  if (typeof r.creator === "string") s.creator = r.creator;
  if (typeof r.seat === "number" && Number.isInteger(r.seat) && r.seat >= 0) s.seat = r.seat;
  if (typeof r.gameId === "string") s.gameId = r.gameId;
  if (typeof r.autostart === "boolean") s.autostart = r.autostart;
  if (r.start === "pending") s.start = "pending";
  return s;
}

/**
 * Known tables for one API origin. Storage can be unavailable or full: the in-memory copy keeps the current game
 * usable, and `failed` tells the screen to explain how to keep the invitation.
 */
export class SessionStore {
  failed = false;
  private memory: Session[] = [];
  constructor(readonly origin: string) {
    this.migrate();
  }
  private all(): Record<string, unknown> {
    try {
      const value = JSON.parse(localStorage.getItem(STORE_KEY) ?? "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  }
  private stored(): Session[] {
    const mine = this.all()[this.origin];
    return (Array.isArray(mine) ? mine : []).map(cleanSession).filter((s): s is Session => !!s);
  }
  private write(sessions: Session[]) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({ ...this.all(), [this.origin]: sessions.slice(0, MAX_KNOWN) }));
      return true;
    } catch {
      return false;
    }
  }
  private readLegacy() {
    try {
      return cleanSession(JSON.parse(localStorage.getItem(LEGACY_KEY) || "null"));
    } catch {
      return null; // an unreadable legacy value is ignored
    }
  }
  /**
   * Older versions kept one unscoped "last table". Browser storage belongs to the page, so it is only known to belong
   * to the API origin when the page talks to its own origin; for any other API it waits for an explicit claim.
   */
  private migrate() {
    const legacy = this.readLegacy();
    if (!legacy || this.origin !== location.origin) return;
    this.adopt(legacy);
  }
  private adopt(legacy: Session) {
    try {
      const known = this.stored();
      if (known.some(s => s.table === legacy.table) || this.write([...known, legacy])) localStorage.setItem(LEGACY_KEY, "");
    } catch { /* storage is unavailable; the record stays where it is */ }
  }
  /** An unscoped record this API origin has not claimed. Its credentials are not used until the owner claims it. */
  legacy(): Session | null {
    const legacy = this.origin === location.origin ? null : this.readLegacy();
    return legacy && !this.get(legacy.table) ? legacy : null;
  }
  claimLegacy() {
    const legacy = this.legacy();
    if (legacy) this.adopt(legacy);
  }
  /** This tab's session for the table, but only if it was saved for this API origin. */
  tab(table: string): Session | null {
    try {
      const raw = JSON.parse(sessionStorage.getItem(TAB_KEY) || "null");
      const s = cleanSession(raw);
      const scoped = raw?.origin === this.origin || (raw?.origin === undefined && this.origin === location.origin);
      return s && s.table === table && scoped ? s : null;
    } catch {
      return null;
    }
  }
  list(): Session[] {
    const seen = new Set<string>();
    return [...this.memory, ...this.stored()].filter(s => !seen.has(s.table) && !!seen.add(s.table));
  }
  get(table: string) {
    return this.list().find(s => s.table === table);
  }
  /** Tables this browser can act in (owner or seat). A table that was only watched is not "known" in the room. */
  owned(): Session[] {
    return this.list().filter(s => s.credential || s.creator);
  }
  /** Retains an issued capability without replacing the table currently open in this tab. */
  remember(session: Session) {
    const entry = cleanSession(session)!;
    this.memory = [entry, ...this.memory.filter(s => s.table !== entry.table)].slice(0, MAX_KNOWN);
    const ok = this.write([entry, ...this.stored().filter(s => s.table !== entry.table)]);
    this.failed = !ok;
    return ok;
  }
  save(session: Session) {
    const entry = cleanSession(session)!;
    let ok = this.remember(entry);
    try {
      sessionStorage.setItem(TAB_KEY, JSON.stringify({ ...entry, origin: this.origin }));
    } catch {
      ok = false;
    }
    this.failed = !ok;
    return ok;
  }
  /** Forgetting removes every copy of the capability: the index, this tab and the unscoped legacy record. */
  forget(table: string) {
    this.memory = this.memory.filter(s => s.table !== table);
    this.write(this.stored().filter(s => s.table !== table));
    try {
      if (JSON.parse(sessionStorage.getItem(TAB_KEY) || "null")?.table === table) sessionStorage.setItem(TAB_KEY, "");
    } catch { /* nothing readable to clear */ }
    try {
      if (this.readLegacy()?.table === table) localStorage.setItem(LEGACY_KEY, "");
    } catch { /* nothing readable to clear */ }
  }
}

export interface RoomHost {
  origin: string;
  store: SessionStore;
  alive: () => boolean;
  request: <T = any>(path: string, body?: unknown, credential?: string, signal?: AbortSignal) => Promise<T>;
  open: (session: Session, reconnect: boolean, intent?: Intent) => void;
  go: (page: Page, params?: Record<string, string>) => void;
  fail: (message: string) => void;
  clearError: () => void;
  exit?: () => void;
  navigation?: Pick<NavigationOptions, "sections" | "selectSection">;
  /** Host-supplied catalog entries must be playable by this API, or prepared by prepareSeat. */
  extraModels?: (gameId: string, signal: AbortSignal) => Promise<BotDescriptor[]>;
  prepareSeat?: (gameId: string, choiceId: string, options: Record<string, unknown>, signal: AbortSignal) => Promise<string | { choice_id: string; options?: Record<string, unknown> }>;
  initialPage?: "play" | "watch";
  showNavigation?: boolean;
  /** Opens the new-table form with this game and one seat already chosen (for example "play with this model"). */
  preselect?: SeatPreselect;
  /** The page's state from the address (#/play?page=&game=&status=&new=); setAddress keeps it there. */
  params?: Record<string, string>;
  setAddress?: (state: Record<string, string>) => void;
  /** The picker heading of any catalog entry (also one the server lists itself); undefined keeps its own group. */
  groupFor?: (descriptor: BotDescriptor) => string | undefined;
}

/** A host's request to prefill one seat of the new-table form. Nothing private: only public game and choice IDs. */
export interface SeatPreselect {
  gameId: string;
  /** The seat kind; for "bot" or "model" the choice ID names the entry (a built-in bot or an extraModels descriptor). */
  kind?: "human" | "agent" | "bot" | "model" | "empty";
  choiceId?: string;
  /** Zero-based seat card; by default the first seat that is not the human's own. */
  seat?: number;
}

const STATUS_TEXT: Record<string, string> = { lobby: "waiting for players", playing: "in play", completed: "finished" };
const INITIAL_SEATS: Record<string, string[]> = {
  dice_circuit: ["human", "greedy"], rentcraft: ["human", "collector", "miser"],
};
const shortId = (id: string) => id.slice(0, 8);
const countText = (info: GameInfo) =>
  info.min_players === info.max_players ? `${info.min_players} ${info.min_players === 1 ? "player" : "players"}` : `${info.min_players}-${info.max_players} players`;
const ANIMAL_EMOJI = ["🐘", "🦒", "🦁", "🦏", "🦓", "🦛", "🐊", "🦍"];
const ANIMALS = ["elephant", "giraffe", "lion", "rhinoceros", "zebra", "hippopotamus", "crocodile", "gorilla"];
const animalEmoji = (token: string | undefined, seat: number) => { const k = ANIMALS.indexOf(token ?? ""); return ANIMAL_EMOJI[k >= 0 ? k : seat % ANIMAL_EMOJI.length]; };
const NAME_KEY = "boardcraft.player-name";
const savedName = () => { try { return localStorage.getItem(NAME_KEY) ?? ""; } catch { return ""; } };
const saveName = (name: string) => { try { localStorage.setItem(NAME_KEY, name); } catch { /* this visit only */ } };
/** The Name field before the creator types: the remembered name on the first human seat, otherwise empty. */
const defaultSeatName = (card: number, kind: string) => kind === "human" && card === 0 ? savedName() || "Player" : "";
/** Decision areas of a combined (hybrid) Rentcraft player and what each one decides. */
export const HYBRID_AREAS: [string, string, string][] = [
  ["buy", "purchase", "buying the space the player landed on"], ["auctions", "auctions", "auctions"],
  ["management", "management", "the turn (building, selling, mortgages, jail), paying a debt, a taken-over mortgaged space"],
  ["offers", "offers", "making trade offers: in offer windows and at any other moment"], ["responses", "responses", "answering other players' offers"],
];
/** The `hybrid:` choice the server pins: overridden areas, then the rest. */
export function hybridChoice(rest: string, areas: Record<string, string>) {
  if (!rest) throw new Error("Combined player: choose the base player for the remaining areas.");
  return `hybrid:${[...HYBRID_AREAS.filter(([a]) => areas[a] && areas[a] !== rest).map(([a]) => `${a}=${areas[a]}`), `rest=${rest}`].join(";")}`;
}
function seatText(seat: TableSummary["seats"][number]) {
  const kind = seat.kind === "human" ? "human" : seat.kind === "agent" ? "agent" : "bot";
  const name = seat.kind === "human" || seat.kind === "agent" ? (seat.display_name && seat.display_name !== "Human" && seat.display_name !== "Agent" ? seat.display_name : kind === "human" ? "Human" : "Agent") : seat.display_name ?? seat.kind;
  return `${name} · ${kind}${seat.occupied ? "" : " (free)"}`;
}
/** The note under a seat's fields (as in the shared design): the agent box, a model's description, a free seat. */
function describeChoice(kind: string, entry?: BotDescriptor) {
  if (kind === "empty") return "Seat free.";
  if (kind === "agent") return "The agent joins after the table is created. You get a ready sentence to paste to the agent.";
  if (kind !== "model") return "";
  return [entry?.description ?? "", entry?.training ? "The model is still training - it plays the version from the chosen save." : ""].filter(Boolean).join(" ");
}

/** Catalog entries of one kind in their groups: the host's `group` heading, or the default heading of the kind. */
function catalogGroups(descriptors: BotDescriptor[], kind: "builtin" | "model", groupFor?: (d: BotDescriptor) => string | undefined): [string, BotDescriptor[]][] {
  const groups = new Map<string, BotDescriptor[]>();
  for (const d of descriptors.filter(item => item.kind === kind)) {
    const title = groupFor?.(d)?.trim() || d.group?.trim() || (kind === "model" ? "Trained models" : "Built-in bots");
    groups.set(title, [...(groups.get(title) ?? []), d]);
  }
  return [...groups];
}

/** The room: the games, the listed tables and the new-table form (as in the shared design). */
export function mountRoom(parent: HTMLElement, host: RoomHost) {
  const life = new AbortController();
  const alive = () => host.alive() && !life.signal.aborted;
  const page = element("section", "bc-hub");
  if (host.showNavigation !== false) page.append(renderNavigation({ current: host.initialPage ?? "play", go: host.go, exit: host.exit, ...host.navigation,
    rules: () => showRules(),
  }));
  const main = element("div", "bc-room");
  const loading = element("p", "bc-small", "Loading games…");
  loading.setAttribute("role", "status");
  main.append(loading);
  page.append(main);
  parent.append(page);
  let games: GameInfo[] = [];
  const gameName = (id?: string) => games.find(g => g.game_id === id)?.name ?? "Table";
  // The list's page and filters and the open form are kept in the address, so Back and a reload return to them.
  const address: Record<string, string> = { ...(host.params ?? {}) };
  const remember = (patch: Record<string, string>) => {
    Object.assign(address, patch);
    host.setAddress?.(Object.fromEntries(["page", "game", "status", "new"].filter(k => address[k]).map(k => [k, address[k]])));
  };


  function showRules(selected?: string) {
    openRules(page, games, id => host.request<string>("/api/games/" + encodeURIComponent(id) + "/rules", undefined, undefined, life.signal), selected);
  }

  function buildCreate() {
    const form = element("form", "bc-create bc-panel");
    form.append(element("h2", "", "New table"));
    const cards = element("div", "bc-game-cards");
    const prompt = element("p", "bc-small", "Pick a game to set up its table.");
    const body = element("div", "bc-create-body"); body.hidden = true;
    const select = element("select");
    select.setAttribute("aria-label", "Game");
    select.append(Object.assign(element("option", "", "Pick a game"), { value: "" }));
    const cardButtons = new Map<string, HTMLButtonElement>();
    for (const info of games) {
      const option = element("option", "", info.name);
      option.value = info.game_id;
      select.append(option);
      const card = button("", () => {
        select.value = info.game_id;
        gameChanged();
      }, "bc-game-card");
      card.append(gameIcon(info.game_id), element("strong", "", info.name));
      card.title = countText(info); card.setAttribute("aria-pressed", "false");
      cardButtons.set(info.game_id, card);
      cards.append(card);
    }
    const gameLabel = element("label", "", "Game");
    gameLabel.append(select);
    const variantLabel = element("label", "", "Variant");
    const variant = element("select");
    variant.setAttribute("aria-label", "Variant");
    variantLabel.append(variant);
    const countLabel = element("label", "", "Players");
    countLabel.hidden = true;
    const count = element("select");
    count.setAttribute("aria-label", "Number of players");
    countLabel.append(count);
    const basics = element("div", "bc-create-basics");
    basics.append(gameLabel, variantLabel, countLabel);
    const setupRoot = element("div", "bc-create-settings");
    const seats = element("div", "bc-seat-setup");
    // As in the shared design: the seats, one row of the game's options, then the two create buttons. "Create and sit"
    // takes the first human seat; the table starts by itself once every seat is ready (bots at once, people and agents
    // when they sit down).
    const status = element("p", "bc-small bc-form-msg");
    status.setAttribute("role", "status");
    const sitButton = element("button", "bc-primary", "Create and sit");
    sitButton.type = "submit";
    const watchButton = element("button", "", "Create and watch");
    watchButton.type = "button";
    const actions = element("div", "bc-create-actions");
    actions.append(watchButton, sitButton);
    // The chosen game's own mark and a one-line note head the form (as each game's form in the shared design).
    const formHead = element("div", "form-head");
    body.append(formHead, basics, seats, setupRoot, status, actions);
    // "Game" above the picker, as the shared design's filter-bar field.
    const pickLabel = element("span", "fl bc-pick-label", "Game");
    form.append(pickLabel, cards, prompt, body);
    gameLabel.hidden = true;

    let seatSelects: HTMLSelectElement[] = [];
    const seatNamesDraft: string[] = [], seatAnimals: (string | undefined)[] = [];
    const seatHybrids: { rest: string; areas: Record<string, string> }[] = [];
    let descriptors: BotDescriptor[] = [];
    let tableFields: ReturnType<typeof settingsFields>, liveFields: ReturnType<typeof settingsFields>;
    const selections = new Map<string, string[]>();
    let catalogReady = false, catalogEpoch = 0, busy = false;
    let drawnCount = 0;
    let catalogController = new AbortController();
    life.signal.addEventListener("abort", () => catalogController.abort(), { once: true });
    const info = () => games.find(g => g.game_id === select.value)!;
    const humanSeats = () => seatSelects.flatMap((s, i) => (s.value === "human" ? [i] : []));
    /** The catalog entry of a choice: the model itself, or the model one of its saved versions belongs to. */
    const modelOf = (id: string) => descriptors.find(d => d.choice_id === id || d.versions?.some(v => v.choice_id === id));
    const lock = () => {
      for (const b of [sitButton, watchButton]) b.disabled = !catalogReady || busy || !info();
    };
    const refreshActions = () => {
      const humans = humanSeats();
      sitButton.hidden = humans.length === 0;
      sitButton.type = humans.length ? "submit" : "button";
      watchButton.type = humans.length ? "button" : "submit";
      lock();
    };
    const setup = () => {
      const selected = selections.get(select.value) ?? [];
      seats.replaceChildren();
      seatSelects = [];
      const groups: [string, { choice_id: string; display_name: string }[]][] = [
        ["People", [{ choice_id: "human", display_name: "Human" }]],
        ["Agents", [{ choice_id: "agent", display_name: "Agent" }]],
        ...catalogGroups(descriptors, "builtin", host.groupFor),
        ...catalogGroups(descriptors, "model", host.groupFor),
        ...(select.value === "rentcraft" ? [["Combined", [{ choice_id: "hybrid", display_name: "Combined player" }]] as [string, { choice_id: string; display_name: string }[]]] : []),
        ["Unused", [{ choice_id: "empty", display_name: "Empty" }]],
      ];
      const ids = groups.flatMap(([, items]) => items.map(item => item.choice_id));
      const preferred = descriptors.find(d => d.kind === "builtin" && d.choice_id === "greedy") ?? descriptors.find(d => d.kind === "builtin");
      const defaults = INITIAL_SEATS[select.value] ?? [];
      for (let i = 0; i < info().max_players; i++) {
        const card = element("div", "bc-seat-card");
        // The seat card is one group for assistive technology; the pawn and "SEAT N" head it, as in the shared design.
        card.setAttribute("role", "group");
        card.setAttribute("aria-label", `Seat ${i + 1}`);
        const top = element("div", "bc-seat-top");
        const label = element("label", "bc-seat-select");
        const s = element("select");
        s.setAttribute("aria-label", `Player ${i + 1} type`);
        for (const [title, items] of groups) {
          if (!items.length) continue;
          const group = element("optgroup");
          group.label = title;
          for (const item of items) {
            const opt = element("option", "", item.display_name);
            opt.value = item.choice_id;
            group.append(opt);
          }
          s.append(group);
        }
        for (const d of descriptors) for (const v of d.versions ?? [])
          if (![...s.options].some(o => o.value === v.choice_id)) s.append(Object.assign(element("option", "", v.label), { value: v.choice_id }));
        s.value = i >= Number(count.value) ? "empty" : selected[i] && ids.includes(selected[i]) ? selected[i] :
          defaults[i] && ids.includes(defaults[i]) ? defaults[i] : i === 0 ? "human" : (preferred?.choice_id ?? "human");
        s.hidden = true;
        const pawn = element("span", "bc-seat-pawn", select.value === "rentcraft" ? ANIMAL_EMOJI[Math.max(0, ANIMALS.indexOf(seatAnimals[i] ?? ANIMALS[i]))] : String(i + 1));
        pawn.style.setProperty("--seat-colour", `var(--seat-${i + 1})`);
        top.append(pawn, element("span", "bc-seat-num", `SEAT ${i + 1}`));
        const tabs = element("div", "bc-seat-types");
        tabs.setAttribute("role", "group");
        tabs.setAttribute("aria-label", `Player ${i + 1} seat type`);
        const pickerLabel = element("label"), picker = element("select");
        const caption = element("span"); pickerLabel.append(caption, picker);
        picker.setAttribute("aria-label", `Player ${i + 1} bot or model`);
        const rentcraft = select.value === "rentcraft";
        const kinds = rentcraft ? ["human", "bot", "agent", "model", "hybrid", "empty"] : ["human", "bot", "agent", "model", "empty"];
        tabs.style.setProperty("--n", String(kinds.length));
        const kindOf = () => ["human", "agent", "empty", "hybrid"].includes(s.value) ? s.value : modelOf(s.value)?.kind === "model" ? "model" : "bot";
        const syncKind = () => {
          const kind = kindOf();
          tabs.querySelectorAll<HTMLElement>("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.kind === kind)));
          pickerLabel.hidden = !["bot", "model"].includes(kind);
          caption.textContent = kind === "model" ? "Model" : rentcraft ? "Personality" : "Bot";
          // The creator names people and agents (an agent seat left empty lets the agent name itself when it joins).
          nameLabel.hidden = kind !== "human" && kind !== "agent";
          if (seatNamesDraft[i] === undefined) nameInput.value = defaultSeatName(i, kind);
          nameInput.placeholder = kind === "agent" ? "Empty: the agent names itself" : "";
          hybridBox.hidden = kind !== "hybrid";
          animals.hidden = !rentcraft || kind === "empty";
          const entries = catalogGroups(descriptors, kind === "model" ? "model" : "builtin", host.groupFor);
          picker.replaceChildren(...entries.flatMap(([title, items]) => {
            const options = items.map(d => Object.assign(element("option", "", d.display_name), { value: d.choice_id }));
            if (entries.length < 2) return options;
            const group = element("optgroup");
            group.label = title;
            group.append(...options);
            return [group];
          }));
          picker.value = modelOf(s.value)?.choice_id ?? s.value;
        };
        for (const kind of kinds) {
          const [icon, text] = ({ human: ["👤", "Human"], bot: ["🤖", "Bot"], agent: ["🔌", "MCP/API agent"], model: ["🧠", "RL model"], hybrid: ["🧩", "Combined player"], empty: ["∅", "Empty"] } as Record<string, string[]>)[kind];
          const b = button("", () => {
            if (kind === kindOf()) return;
            s.value = ["human", "agent", "empty", "hybrid"].includes(kind) ? kind : descriptors.find(d => d.kind === (kind === "model" ? "model" : "builtin"))?.choice_id ?? "";
            s.dispatchEvent(new Event("change"));
          });
          b.append(element("b", "", icon), text);
          b.setAttribute("aria-label", text);
          b.dataset.kind = kind;
          b.disabled = ["bot", "model"].includes(kind) && !descriptors.some(d => d.kind === (kind === "model" ? "model" : "builtin"));
          tabs.append(b);
        }
        picker.onchange = () => { s.value = picker.value; s.dispatchEvent(new Event("change")); };
        // A person's or agent's name (shown at the table), the Rentcraft piece and the combined player's areas.
        const nameLabel = element("label", "bc-seat-name", "Name");
        const nameInput = element("input");
        nameInput.maxLength = 40;
        nameInput.setAttribute("aria-label", `Player ${i + 1} name`);
        nameInput.value = seatNamesDraft[i] ?? defaultSeatName(i, kindOf());
        nameInput.oninput = () => { seatNamesDraft[i] = nameInput.value; };
        nameLabel.append(nameInput);
        const animals = element("div", "animals");
        animals.setAttribute("role", "group");
        animals.setAttribute("aria-label", `Player ${i + 1} piece`);
        const drawAnimals = () => {
          const mine = seatAnimals[i] ?? ANIMALS[i];
          const taken = new Set(seatAnimals.filter((a, j) => j !== i && seatSelects[j]?.value !== "empty").map((a, j) => a ?? ANIMALS[j]));
          animals.replaceChildren(...ANIMALS.map((a, k) => {
            const b = button(ANIMAL_EMOJI[k], () => { if (taken.has(a)) return; seatAnimals[i] = a; seats.querySelectorAll(".animals").forEach(x => (x as any).redraw?.()); pawn.textContent = ANIMAL_EMOJI[k]; }, a === mine ? "on" : taken.has(a) ? "taken" : "");
            b.title = a; b.setAttribute("aria-label", a); b.setAttribute("aria-pressed", String(a === mine)); b.disabled = taken.has(a) && a !== mine;
            return b;
          }));
        };
        (animals as any).redraw = drawAnimals;
        if (seatAnimals[i] == null) seatAnimals[i] = ANIMALS[i];
        drawAnimals();
        const hybridBox = element("div", "bc-hybrid");
        const playerOptions = (value: string, blank?: string) => {
          const sel = element("select");
          if (blank) sel.append(Object.assign(element("option", "", blank), { value: "" }));
          for (const [title, items] of [...catalogGroups(descriptors, "builtin", host.groupFor), ...catalogGroups(descriptors, "model", host.groupFor)]) {
            const og = element("optgroup"); og.label = title;
            for (const d of items) og.append(Object.assign(element("option", "", d.display_name), { value: d.choice_id }));
            sel.append(og);
          }
          sel.value = value;
          return sel;
        };
        const hybrid = seatHybrids[i] ??= { rest: "", areas: {} };
        if (!hybrid.rest) hybrid.rest = descriptors.find(d => d.kind === "builtin")?.choice_id ?? "";
        const restSelect = playerOptions(hybrid.rest);
        restSelect.setAttribute("aria-label", `Player ${i + 1} combined player: the remaining areas`);
        restSelect.onchange = () => { hybrid.rest = restSelect.value; };
        const restLabel = element("label", "", "All other decisions"); restLabel.append(restSelect);
        hybridBox.append(restLabel);
        for (const [area, label, help] of HYBRID_AREAS) {
          const sel = playerOptions(hybrid.areas[area] ?? "", "same as above");
          sel.setAttribute("aria-label", `Player ${i + 1} combined player: ${label}`);
          sel.onchange = () => { hybrid.areas[area] = sel.value; };
          const l = element("label", "", label); l.title = help; l.append(sel);
          hybridBox.append(l);
        }
        const help = element("small", "bc-seat-help");
        // A model with several saved versions (final, checkpoints, latest save) offers them in its own select.
        const versionLabel = element("label", "bc-seat-version", "Version"), version = element("select");
        version.setAttribute("aria-label", `Player ${i + 1} model version`);
        versionLabel.append(version);
        version.onchange = () => { s.value = version.value; s.dispatchEvent(new Event("change")); };
        // A bot seat is only the choice of the bot (the shared design has no per-seat bot settings): it plays with its
        // own settings.
        const drawOptions = () => {
          const kind = kindOf(), entry = modelOf(s.value);
          help.textContent = describeChoice(kind, entry);
          help.className = kind === "agent" ? "bc-seat-help mcpbox" : "bc-seat-help";
          help.hidden = !help.textContent;
          versionLabel.hidden = !entry?.versions?.length;
          if (entry?.versions?.length) {
            const list = entry.versions.some(v => v.choice_id === entry.choice_id) ? entry.versions
              : [{ choice_id: entry.choice_id, label: entry.training ? "latest save" : "final" }, ...entry.versions];
            version.replaceChildren(...list.map(v => Object.assign(element("option", "", v.label), { value: v.choice_id })));
            version.value = s.value;
          }
          card.classList.toggle("bc-seat-empty", s.value === "empty");
          syncKind();
        };
        drawOptions();
        s.onchange = () => {
          drawOptions();
          const saved = selections.get(select.value) ?? [];
          seatSelects.forEach((item, index) => { if (index < drawnCount) saved[index] = item.value; });
          selections.set(select.value, saved);
          refreshActions();
        };
        label.append(s);
        label.hidden = true;
        card.append(top, label, tabs, pickerLabel, versionLabel, nameLabel, hybridBox, help, animals);
        seats.append(card);
        seatSelects.push(s);
      }
      refreshActions();
      drawnCount = Number(count.value);
    };
    const gameChanged = () => {
      const epoch = ++catalogEpoch;
      catalogController.abort();
      catalogController = new AbortController();
      const discoverySignal = catalogController.signal;
      catalogReady = false;
      descriptors = [];
      seats.hidden = true;
      setupRoot.replaceChildren();
      selections.clear();
            lock();
      status.textContent = "Loading available bots…";
      const g = info();
      body.hidden = !g; prompt.hidden = !!g;
      if (!g) return;
      remember({ new: g.game_id });
      for (const [id, card] of cardButtons) card.setAttribute("aria-pressed", String(id === g.game_id));
      formHead.replaceChildren(gameMark(g.game_id, g.name), element("span", "bc-small", countText(g)));
      variant.replaceChildren(...g.variants.map(v => Object.assign(element("option", "", v), { value: v })));
      variantLabel.hidden = g.variants.length < 2;
      count.replaceChildren();
      for (let n = g.min_players; n <= g.max_players; n++) {
        const opt = element("option", "", n === 1 ? "Solo" : `${n} players`);
        opt.value = String(n);
        count.append(opt);
      }
      count.value = String(Math.max(g.min_players, Math.min(g.max_players, INITIAL_SEATS[g.game_id]?.length ?? g.min_players)));
      setup();
      const path = "/api/games/" + encodeURIComponent(g.game_id);
      void Promise.all([
        host.request<BotDescriptor[]>(path + "/bots", undefined, undefined, discoverySignal),
        host.request<Settings>(path + "/settings", undefined, undefined, discoverySignal),
        ...(host.extraModels ? [host.extraModels(g.game_id, discoverySignal)] : []),
      ]).then(([builtins, settings, extra]) => {
        const items = [...builtins as BotDescriptor[], ...(extra as BotDescriptor[] | undefined ?? [])];
        if (!alive() || epoch !== catalogEpoch) return;
        if (!Array.isArray(items) || items.some(item => !item || typeof item.choice_id !== "string" || typeof item.display_name !== "string" || !["builtin", "model"].includes(item.kind)) ||
          new Set(items.map(item => item.choice_id)).size !== items.length)
          throw new Error("Available bots could not be loaded. Choose the game again to retry.");
        const ruleCount = Object.keys((settings as Settings).table?.properties ?? {}).length;
        formHead.lastElementChild!.textContent = `${countText(g)} · ${ruleCount ? "official rules" : "no extra rules to set"}`;
                const tableRoot = element("div"), liveRoot = element("div");
        tableFields = settingsFields(tableRoot, "Game options", (settings as Settings).table, {}, {}, undefined, true);
        liveFields = settingsFields(liveRoot, "Time and bots", (settings as Settings).live, {}, {}, undefined, true);
        // Every option is in sight at once, as one row of fields in the shared design (in the schema's `order`).
        setupRoot.classList.add("bc-options-row", "rules-row");
        setupRoot.replaceChildren(...tableRoot.querySelectorAll(".bc-setting"), ...liveRoot.querySelectorAll(".bc-setting"));
        descriptors = items;
        selections.clear();
        catalogReady = true;
        lock();
        status.textContent = "";
        setup();
        seats.hidden = false;
        applyPreselect(g.game_id);
      }).catch(() => {
        if (!alive() || epoch !== catalogEpoch || discoverySignal.aborted) return;
        status.textContent = "Available bots could not be loaded. Choose the game again to retry.";
      });
    };
    select.onchange = gameChanged;
    let pending = host.preselect;
    /** Prefills the host's chosen seat once, when that game's catalog is ready; an unknown choice is reported, not guessed. */
    function applyPreselect(gameId: string) {
      const want = pending;
      if (!want || want.gameId !== gameId) return;
      pending = undefined;
      const choice = want.choiceId ?? (want.kind && ["human", "agent", "empty"].includes(want.kind) ? want.kind : undefined);
      const known = choice && (["human", "agent", "empty"].includes(choice) || descriptors.some(d => d.choice_id === choice && (!want.kind || want.kind === (d.kind === "model" ? "model" : "bot"))));
      if (!known) {
        status.textContent = "The chosen bot or model is not available for this game.";
        return;
      }
      const index = want.seat ?? Math.max(0, seatSelects.findIndex((item, i) => i > 0 && item.value !== "human"));
      const target = seatSelects[Math.min(index, seatSelects.length - 1)];
      if (index >= Number(count.value) && index < info().max_players) {
        count.value = String(index + 1);
        count.dispatchEvent(new Event("change"));
      }
      const seatSelect = seatSelects[Math.min(index, seatSelects.length - 1)] ?? target;
      seatSelect.value = choice!;
      seatSelect.dispatchEvent(new Event("change"));
      form.scrollIntoView?.({ block: "start" });
    }
    if (pending && games.some(g => g.game_id === pending!.gameId)) {
      select.value = pending.gameId;
      gameChanged();
    } else if (address.new && games.some(g => g.game_id === address.new)) {
      select.value = address.new;
      gameChanged();
    }
    count.onchange = () => {
      const selected = selections.get(select.value) ?? [];
      seatSelects.forEach((item, index) => { if (index < drawnCount) selected[index] = item.value; });
      selections.set(select.value, selected);
      setup();
    };
    lock();

    async function create(mode: "sit" | "watch") {
      if (!alive() || !catalogReady || busy) return;
      busy = true;
      lock();
      host.clearError();
      try {
        const g = info();
        const values = seatSelects.flatMap((s, i) => s.value === "empty" ? [] : [{
          id: s.value === "hybrid" ? hybridChoice(seatHybrids[i]?.rest ?? "", seatHybrids[i]?.areas ?? {}) : s.value,
          options: {}, card: i }]);
        if (values.length < g.min_players) throw Error(`At least ${g.min_players} ${g.min_players === 1 ? "player" : "players"} must sit at the table.`);
        if (values.length > g.max_players) throw Error(`At most ${g.max_players} players can sit at the table.`);
        const submittedVariant = variant.value || g.variants[0];
        const settings = tableFields.read(), live = liveFields.read();
        // The whole submission is fixed here; the controls may change while the requests are in flight.
        const ownCard = mode === "sit" ? humanSeats()[0] ?? -1 : -1;
        const own = ownCard < 0 ? -1 : seatSelects.slice(0, ownCard).filter(s => s.value !== "empty").length;
        const wantsAutostart = true;
        const kinds = await Promise.all(values.map(v => host.prepareSeat
          ? host.prepareSeat(g.game_id, v.id, v.options, life.signal) : selection(v.id, v.options)));
        if (!alive()) return;
        // The creator's seat names go with the table; the server checks them and keeps them when someone joins.
        const seatNames = values.map(v => {
          const kind = seatSelects[v.card].value;
          return kind === "human" || kind === "agent" ? (seatNamesDraft[v.card] ?? defaultSeatName(v.card, kind)).trim() || null : null;
        });
        const made = await host.request("/api/tables", {
          config: { game_id: g.game_id, variant: submittedVariant, players: kinds.length, ...(Object.keys(settings).length ? { settings } : {}) },
          seats: kinds,
          ...(seatNames.some(name => name !== null) ? { seat_names: seatNames } : {}),
          ...(Object.keys(live).length ? { live } : {}),
          visibility: "listed",
        }, undefined, life.signal);
        const created: Session = { table: made.table_id, creator: made.creator_credential, gameId: g.game_id, ...(wantsAutostart ? { autostart: true } : {}) };
        host.store.remember(created);
        if (!alive()) return;
        // The Rentcraft pieces are set by the creator before anybody sits down.
        const presentation = values.flatMap((v, apiSeat) => {
          const token = g.game_id === "rentcraft" ? seatAnimals[v.card] ?? ANIMALS[v.card] : undefined;
          return token ? [{ seat: apiSeat, token }] : [];
        });
        if (presentation.length)
          try { await host.request(`/api/tables/${created.table}/presentation`, { seats: presentation }, made.creator_credential, life.signal); }
          catch { /* an older server keeps the default names and pieces */ }
        // The automatic start is the server's: armed now, it starts the table when the last seat is taken.
        if (wantsAutostart)
          try { await host.request(`/api/tables/${created.table}/autostart`, { on: true }, made.creator_credential, life.signal); }
          catch { /* an older server: the creator presses Start */ }
        const firstName = seatNamesDraft[seatSelects.findIndex(x => x.value === "human")];
        if (firstName) saveName(firstName);
        let problem = "";
        if (own >= 0)
          try {
            const joined = await host.request(`/api/tables/${created.table}/join`, { seat: own }, undefined, life.signal);
            created.credential = joined.credential;
            created.seat = own;
            host.store.remember(created);
            if (!alive()) return;
          } catch (e) {
            if (!alive()) return;
            problem = `Your table was created, but taking your seat failed: ${(e as Error).message} Use “Take this seat” on the table screen.`;
          }
        // As in the shared design: the sentence for every agent seat is copied to the clipboard right away.
        const agentSeats = values.flatMap((v, apiSeat) => seatSelects[v.card].value === "agent" ? [apiSeat] : []);
        if (agentSeats.length) {
          const text = agentSeats.map(seat => agentPrompt({ origin: host.origin, gameId: g.game_id, gameName: g.name, table: created.table, seat })).join("\n\n");
          status.textContent = "The sentence for the agent was copied to the clipboard.";
          try { await navigator.clipboard?.writeText(text); }
          catch { /* no clipboard: the waiting window still shows the sentence for each agent seat */ }
        }
        host.open(created, false);
        if (problem) host.fail(problem);
      } catch (e) {
        if (alive() && (e as Error).name !== "AbortError") {
          status.textContent = (e as Error).message;
          host.fail((e as Error).message);
        }
      } finally {
        busy = false;
        if (alive()) lock();
      }
    }
    form.onsubmit = e => {
      e.preventDefault();
      void create(humanSeats().length ? "sit" : "watch");
    };
    watchButton.addEventListener("click", () => {
      if (watchButton.type === "button") void create("watch");
    });
    return form;
  }

  function buildTables() {
    const section = element("section", "bc-tables bc-panel");
    const tablesHead = element("h2", "", "Tables");
    const bulk = button("", () => undefined, "bc-bulk-delete");
    bulk.hidden = true;
    tablesHead.append(element("span", "sp"), bulk);
    section.append(tablesHead);
    const filters = element("div", "bc-table-filters");
    const gameFilter = element("select"), statusFilter = element("select");
    gameFilter.setAttribute("aria-label", "Filter by game");
    statusFilter.setAttribute("aria-label", "Filter by status");
    gameFilter.append(Object.assign(element("option", "", "All games"), { value: "" }), ...games.map(g => Object.assign(element("option", "", g.name), { value: g.game_id })));
    statusFilter.append(...[["", "all"], ...Object.entries(STATUS_TEXT)].map(([v, t]) => Object.assign(element("option", "", t), { value: v })));
    const gameChoices = element("div", "gpicker bc-filter-games"); gameChoices.setAttribute("aria-label", "Game filter"); gameChoices.setAttribute("role", "group");
    for (const g of [{ game_id: "", name: "All games" }, ...games]) {
      const b = button("", () => { gameFilter.value = g.game_id; gameFilter.dispatchEvent(new Event("change")); });
      if (g.game_id) b.append(gameIcon(g.game_id, 16));
      b.append(g.name); b.dataset.game = g.game_id; b.setAttribute("aria-pressed", String(!g.game_id)); gameChoices.append(b);
    }
    gameFilter.hidden = true;
    const field = (label: string, ...controls: HTMLElement[]) => {
      const box = element("div", "ff"); box.append(element("span", "fl", label), ...controls); return box;
    };
    filters.className = "fbar bc-table-filters";
    filters.append(field("Game", gameChoices, gameFilter), field("Status", statusFilter));
    const list = element("div", "bc-table-list");
    const pager = element("div", "bc-pager");
    section.append(filters, list, pager);
    if (games.some(g => g.game_id === address.game)) gameFilter.value = address.game;
    if (address.status && address.status in STATUS_TEXT) statusFilter.value = address.status;
    gameChoices.querySelectorAll<HTMLElement>("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.game === gameFilter.value)));
    let offset = Math.max(0, (Number(address.page) || 1) - 1) * PAGE_SIZE, epoch = 0, controller = new AbortController();
    const keepPlace = () => remember({ game: gameFilter.value, status: statusFilter.value, page: offset ? String(offset / PAGE_SIZE + 1) : "" });
    bindPager(pager, delta => { offset = Math.max(0, offset + delta * PAGE_SIZE); keepPlace(); void load(); });
    let refreshing = false;
    const timer = setInterval(() => { if (!refreshing) void load(true); }, 2000);
    life.signal.addEventListener("abort", () => clearInterval(timer), { once: true });
    life.signal.addEventListener("abort", () => controller.abort(), { once: true });
    const filtered = () => !!gameFilter.value || !!statusFilter.value;
    async function load(quiet = false) {
      refreshing = true;
      const mine = ++epoch;
      controller.abort();
      controller = new AbortController();
      const signal = controller.signal;
      const query = new URLSearchParams();
      if (gameFilter.value) query.set("game_id", gameFilter.value);
      if (statusFilter.value) query.set("status", statusFilter.value);
      query.set("offset", String(offset));
      query.set("limit", String(PAGE_SIZE));
      const wait = element("p", "bc-small", "Loading open tables…");
      wait.setAttribute("role", "status");
      if (!quiet) { list.replaceChildren(wait); pager.replaceChildren(); }
      try {
        const data = await host.request<TableList>("/api/tables?" + query, undefined, undefined, signal);
        if (!alive() || mine !== epoch) return;
        if (!Array.isArray(data?.items) || !Number.isInteger(data.total) || data.items.some(t => !isTableId(t?.table_id) || !Array.isArray(t.seats)))
          throw new Error("Unexpected table list.");
        if (!data.items.length && offset > 0) {
          offset = 0;
          keepPlace();
          return void load();
        }
        render(data);
      } catch {
        if (!alive() || mine !== epoch || signal.aborted) return;
        const failed = element("div", "bc-empty");
        failed.setAttribute("role", "alert");
        failed.append(element("p", "", "Open tables could not be loaded."), button("Try again", () => void load()));
        list.replaceChildren(failed); pager.replaceChildren();
      } finally { if (mine === epoch) refreshing = false; }
    }
    /** Deletes tables this browser created (the server keeps finished games in the replays). */
    async function remove(items: { id: string; creator?: string }[]) {
      for (const item of items)
        await host.request(`/api/tables/${item.id}/delete`, {}, item.creator, life.signal).catch(() => host.fail("A table could not be deleted. Refresh and try again."));
      listKey = "";
      void load();
    }
    let listKey = "";
    function render(data: TableList) {
      const finished = data.items.flatMap(t => {
        const known = host.store.owned().find(s => s.table === t.table_id);
        return t.status === "completed" && (known?.creator || t.can_manage) ? [{ id: t.table_id, creator: known?.creator }] : [];
      });
      bulk.hidden = !finished.length;
      bulk.textContent = `🗑 Delete finished (${finished.length})`;
      bulk.onclick = () => { if (confirm(`Delete ${finished.length} finished tables? The game recordings (replays) stay.`)) void remove(finished); };
      const key = JSON.stringify([data, host.store.owned()]);
      if (key === listKey && list.querySelector(".bc-rows")) return;
      listKey = key;
      list.replaceChildren(); pager.replaceChildren();
      if (!data.items.length) {
        list.append(element("p", "bc-empty", filtered()
          ? "No tables match these filters."
          : "There are no tables yet. Create the first one!"));
        return;
      }
      const rows = element("ul", "bc-rows");
      for (const t of data.items) {
        const row = element("li", `bc-table-row table-row ${t.status}`);
        const name = gameName(t.game_id);
        const known = host.store.owned().find(s => s.table === t.table_id);
        const head = element("div", "bc-table-head");
        const title = element("strong", "", t.number != null ? `Table #${t.number}` : `Table ${shortId(t.table_id)}`);
        title.title = `Table ${t.table_id}`;
        const round = t.status === "playing" && t.round ? ` · round ${t.round}` : "";
        head.append(gameIcon(t.game_id), title, element("small", "", name),
          element("span", `tag bc-badge bc-badge-${t.status}`, (STATUS_TEXT[t.status] ?? t.status) + round));
        const seatList = element("ul", "bc-seat-chips chips");
        for (const s of t.seats) {
          const chip = element("li", `chip${s.occupied ? "" : " empty"}`);
          const pawn = element("span", `av pcbg pc-${s.seat % 4}`, t.game_id === "rentcraft" ? animalEmoji(s.token, s.seat) : "");
          chip.append(pawn, seatText(s));
          seatList.append(chip);
        }
        const act = element("div", "bc-table-actions");
        const go = (label: string, intent?: Intent, primary = false) => {
          // The capability is looked up when pressed, so a table forgotten meanwhile is opened without it.
          const b = button(label, () => host.open(host.store.get(t.table_id) ?? { table: t.table_id, gameId: t.game_id }, true, intent), primary ? "bc-primary" : "");
          b.setAttribute("aria-label", `${label} ${name} table ${shortId(t.table_id)}`);
          return b;
        };
        const canJoin = t.status === "lobby" && t.seats.some(s => s.kind === "human" && !s.occupied);
        if (t.status === "completed") {
          const winner = typeof t.winner === "number" ? t.seats.find(s => s.seat === t.winner)?.display_name ?? `Player ${t.winner + 1}` : null;
          if (winner) act.append(element("span", "bc-small", `won by ${winner}`));
          const replays = button("▶ Replays", () => host.go("replays", { game: t.game_id }));
          replays.setAttribute("aria-label", `${name} replays`);
          act.append(replays, known ? go("Resume", undefined, true) : go("Open", "review"));
        } else if (known) act.append(go("Resume", undefined, true));
        else {
          if (canJoin) act.append(go("Sit down", "join", true));
          act.append(go("Watch", "watch"));
        }
        if ((known?.creator || t.can_manage) && t.status !== "playing") {
          const del = button("🗑", () => {
            if (!confirm(`Delete table ${shortId(t.table_id)}? The game recording (replay) stays.`)) return;
            void remove([{ id: t.table_id, creator: known?.creator }]);
          }, "del");
          del.title = "Delete the table"; del.setAttribute("aria-label", `Delete ${name} table ${shortId(t.table_id)}`);
          act.append(del);
        }
        row.append(head, seatList, act);
        rows.append(row);
      }
      list.append(rows);
      pager.innerHTML = pagerHtml({ items: data.items, total: data.total, page: Math.floor(data.offset / PAGE_SIZE), pages: Math.max(1, Math.ceil(data.total / PAGE_SIZE)) }, "tables", PAGE_SIZE);
      pager.querySelector('[data-page="-1"]')?.setAttribute("aria-label", "Previous page");
      pager.querySelector('[data-page="1"]')?.setAttribute("aria-label", "Next page");
    }
    gameFilter.onchange = statusFilter.onchange = () => {
      gameChoices.querySelectorAll<HTMLElement>("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.game === gameFilter.value)));
      offset = 0;
      keepPlace();
      void load();
    };
    void load();
    return section;
  }

  function showRoom() {
    if (!games.length) {
      main.replaceChildren(element("p", "bc-empty", "This server offers no games yet."));
      return;
    }
    const wide = element("div", "bc-room-grid");
    const side = element("div", "bc-room-side");
    side.append(buildTables());
    const available = element("section", "bc-games bc-panel"); available.append(element("h2", "", "Games"));
    for (const g of games) {
      const row = element("div", "bc-game-row"), text = element("div");
      text.append(element("strong", "", g.name), element("small", "bc-small", countText(g)), element("p", "bc-small", g.description ?? ""));
      const rules = button("📖 Rules", () => showRules(g.game_id), "bc-small-btn"); rules.setAttribute("aria-label", g.name + " rules");
      row.append(gameIcon(g.game_id, 28), text, rules); available.append(row);
    }
    side.prepend(available);
    wide.append(side, buildCreate());
    main.replaceChildren(wide);
    if (address.rules === "1") showRules();
  }
  function loadGames() {
    loading.textContent = "Loading games…";
    main.replaceChildren(loading);
    host.request<GameInfo[]>("/api/games", undefined, undefined, life.signal).then(items => {
      if (!alive()) return;
      if (!Array.isArray(items)) throw new Error("Unexpected game list.");
      games = items;
      showRoom();
    }).catch((e: Error) => {
      if (!alive() || e.name === "AbortError") return;
      const failed = element("div", "bc-empty");
      failed.setAttribute("role", "alert");
      failed.append(element("p", "", "The games could not be loaded. Check that this Boardcraft server is running."), button("Try again", loadGames));
      main.replaceChildren(failed);
    });
  }
  loadGames();
  return {
    dispose() {
      life.abort();
      page.remove();
    },
  };
}

export interface OverlayContext {
  session: Session;
  origin: string;
  gameId: string;
  gameName: string;
  table: string;
  storageFailed: () => boolean;
  /** A start request of this visit is in flight or was acknowledged and the table has not moved on yet. */
  starting: () => boolean;
  /** A seat claim of this visit is in flight. */
  taking: () => boolean;
  intent: () => Intent | null;
  take: (seat: number) => void;
  start: () => void;
  setAutostart?: (on: boolean) => void;
  /** Leaves the table for the Play page. */
  back?: () => void;
}
const ready = (s: NonNullable<View["seats"]>[number]) => s.occupied && s.availability?.status !== "unavailable";

/**
 * The "Waiting for players" window over the real board while a table is still in the lobby (as in the shared design):
 * the free seats, the sentence for each agent, and Back to Play. "Sit down" from the list takes the first free human
 * seat by itself; a creator whose table does not start by itself also gets Start now.
 */
export function mountLobbyOverlay(stage: HTMLElement, ctx: OverlayContext) {
  const root = element("div", "modal soft bc-lobby-modal");
  const box = element("div", "modal-box bc-lobby-overlay");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-label", "Table status");
  root.append(box);
  const title = element("h3", "", "Waiting for players");
  const free = element("p", "bc-lobby-free");
  free.setAttribute("role", "status");
  box.append(title, free);
  const help = mountAgentHelp(box, { origin: ctx.origin, gameId: ctx.gameId, gameName: ctx.gameName, table: ctx.table });
  const startBox = element("div", "bc-lobby-start");
  const note = element("p", "bc-small bc-storage-note");
  note.hidden = true;
  const actions = element("div", "btns");
  if (ctx.back) actions.append(button("Back to Play", ctx.back));
  box.append(startBox, note, actions);
  stage.append(root);
  let startKey = "", took = false;
  return {
    update(view: View) {
      const seats = view.seats ?? [];
      const open = seats.filter(seat => !seat.occupied);
      free.textContent = `Free seats: ${open.map(seat => `${seat.seat + 1} (${seat.kind === "agent" ? "agent" : "human"})`).join(", ") || "-"}`;
      // "Sit down" in the list takes the first free human seat once per visit.
      const seatFree = open.find(seat => seat.kind === "human");
      if (!took && ctx.intent() === "join" && !ctx.session.credential && seatFree && !ctx.taking()) {
        took = true;
        ctx.take(seatFree.seat);
      }
      help.update(seats);
      const everyoneReady = seats.length > 0 && seats.every(ready);
      // The creator's browser, or any client when the server says it may manage the table (open management).
      const manager = !!ctx.session.creator || !!view.can_manage;
      const sKey = JSON.stringify([manager, !!ctx.session.autostart, everyoneReady, ctx.starting(), ctx.session.start === "pending"]);
      if (sKey !== startKey) {
        startKey = sKey;
        startBox.replaceChildren();
        if (manager && !ctx.session.autostart) {
          const now = button("Start now", ctx.start, "btn pri bc-primary");
          now.disabled = !everyoneReady || ctx.starting();
          if (ctx.session.start === "pending" && !ctx.starting())
            startBox.append(element("p", "bc-small", "The start was requested but not confirmed yet. If the table stays here, press Start now to ask again."));
          startBox.append(now);
        }
      }
      note.hidden = !ctx.storageFailed();
      note.textContent = ctx.storageFailed()
        ? "Boardcraft could not save this table in this browser. Your seat works only until you close or reload this tab."
        : "";
    },
    dispose() {
      help.dispose();
      root.remove();
    },
  };
}

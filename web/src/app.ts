import { mountGameView } from "./game-view";
import { button, element } from "./dom";
import { ResponseContext } from "./play-types";
import { mountTournaments } from "./tournaments";
import { mountReplays } from "./replays";
import { mountTournamentsPage, type TournamentDraft } from "./tournaments-page";
import { mountRankings } from "./rankings";
import { openRules } from "./ui/rules";
import { renderNavigation, type Page } from "./navigation";
import type { RoomHost } from "./lobby";
import type { NavigationItem } from "./ui/nav";
import { applyPalette, mountPalette } from "./ui/palette";
import { mountGameBar, type GameBar } from "./ui/gamebar";
import { mountReplayBar, type ReplayBar } from "./ui/replaybar";
import { describeDiceEvents } from "./games/dice-circuit/describe";
import { seatNames } from "./model";
import { isTableId, mountLobbyOverlay, mountRoom, originOf, SessionStore, type Intent, type SeatPreselect, type Session } from "./lobby";
import {
  type View,
  type CommandInput, type Settings,
  type ActionList,
  type GameEvent,
} from "./play-types";
export interface PlayAppOptions {
  apiBaseUrl?: string;
  onExit?: () => void;
  sections?: NavigationItem[];
  selectSection?: (id: string) => void;
  /** An embedding host may already render the shared header. */
  showNavigation?: boolean;
  initialPage?: Page;
  extraModels?: RoomHost["extraModels"];
  prepareSeat?: RoomHost["prepareSeat"];
  /** Opens Play with the new-table form prefilled (game and one seat), for example "play with this model". */
  preselect?: SeatPreselect;
  groupFor?: RoomHost["groupFor"];
  /** Opens Tournaments with the new-tournament form prefilled (for example "Do turnieju" of a host). */
  tournamentDraft?: TournamentDraft;
}
/** An HTTP failure with the status the caller may need to treat specially (for example a stale start). */
class RequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}
const noActions: ActionList = { decision_id: null, actions: [] };
interface ReplayFrame {
  observation: NonNullable<View["observation"]>;
  events: GameEvent[];
  position: number;
  total_positions: number;
}
/** A line for the replay timeline; Rentcraft lists the same events in its own "This move" panel. */
const timelineText = (view: View, events: GameEvent[]) => (view.config.game_id === "dice_circuit" ? describeDiceEvents(events, seatNames(view)) : "");
const FRAME_CACHE = 80;
interface ReplayTimeline { total_positions: number; positions: { position: number; events: { kind: string; player: number | null }[]; metrics: Record<string, unknown> | null }[] }
/** Rentcraft's timeline marks: a new complete colour set, a new hotel, an accepted trade, a bankruptcy. */
export function timelineMarks(gameId: string, timeline: ReplayTimeline, names: string[]) {
  const marks: { position: number; tone: string; label: string }[] = [];
  if (gameId !== "rentcraft") return { marks, legend: [] as [string, string][] };
  const who = (seat: number) => names[seat] ?? `Player ${seat + 1}`;
  let before: { sets: number[]; hotels: number[]; bankrupt: boolean[] } | null = null;
  for (const item of timeline.positions) {
    const m = item.metrics as { sets: number[]; hotels: number[]; bankrupt: boolean[] } | null;
    if (m && before) {
      const was = before;
      m.sets.forEach((n, seat) => { if (n > (was.sets[seat] ?? 0)) marks.push({ position: item.position, tone: "green", label: `${who(seat)}: colour set` }); });
      m.hotels.forEach((n, seat) => { if (n > (was.hotels[seat] ?? 0)) marks.push({ position: item.position, tone: "red", label: `${who(seat)}: hotel` }); });
      m.bankrupt.forEach((b, seat) => { if (b && !was.bankrupt[seat]) marks.push({ position: item.position, tone: "silver", label: `${who(seat)}: bankruptcy` }); });
    }
    for (const e of item.events) if (e.kind === "trade") marks.push({ position: item.position, tone: "amber", label: `Trade${e.player != null ? `: ${who(e.player)}` : ""}` });
    if (m) before = m;
  }
  return { marks, legend: [["green", "colour set"], ["red", "hotel"], ["amber", "trade"], ["silver", "bankruptcy"]] as [string, string][] };
}
export function mountPlayApp(root: HTMLElement, options: PlayAppOptions = {}) {
  applyPalette();
  const hadAppClass = root.classList.contains("bc-app");
  root.classList.add("bc-app");
  const base = (options.apiBaseUrl ?? "").replace(/\/$/, "");
  const origin = originOf(base);
  const store = new SessionStore(origin);
  const controller = new AbortController();
  const context = new ResponseContext();
  let session: Session | null = null;
  let game: ReturnType<typeof mountGameView> | null = null;
  let stage: HTMLElement | null = null;
  let overlay: ReturnType<typeof mountLobbyOverlay> | null = null;
  let mountedReadOnly = false;
  let room: ReturnType<typeof mountRoom> | null = null;
  let pages: { dispose(): void } | null = null;
  /** Where the table screen's back button leads (the replay list opens tables, too). */
  let backTo: { page: Page; params: Record<string, string> } | null = null;
  let tournaments: ReturnType<typeof mountTournaments> | null = null;
  const gameNames = new Map<string, string>();
  // Per-visit ownership of requests in flight: an old visit finishing late must not touch the current one.
  let startOwner: Session | null = null;
  let startAcked: Session | null = null;
  let takeOwner: Session | null = null;
  let visitIntent: Intent | null = null;
  let nextDelay = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;
  let polling = false;
  let sending = false;
  let cursor = 0;
  let reconnect = true;
  let last: View | null = null;
  let replay = false;
  let replayRequest = 0;
  let liveEpoch = 0;
  let resources: { spec: unknown; rules: string; settings:Settings } | null = null;
  let errorElement: HTMLElement | null = null;
  let sessionController = new AbortController();
  let bar: GameBar | null = null;
  let pendingPreselect = options.preselect;
  const takePreselect = () => { const value = pendingPreselect; pendingPreselect = undefined; return value; };
  let replayUi: ReplayBar | null = null;
  // Each replay position is recomputed by the server, so frames already read are kept for stepping back and forth.
  let frames = new Map<number, Promise<ReplayFrame>>();
  let timeline: ReplayTimeline | null = null;
  let shownFrame: ReplayFrame | null = null;
  const request = async <T = any>(
    path: string,
    body?: unknown,
    credential?: string,
    signal = sessionController.signal,
  ): Promise<T> => {
    const response = await fetch(base + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(credential ? { Authorization: `Bearer ${credential}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    if(response.status===400 || response.status===422){
      const error=await response.json().catch(()=>({}));
      throw new RequestError(typeof error.detail==="string" ? error.detail : "Check the selected values.", response.status);
    }
    if (!response.ok)
      throw new RequestError(
        response.status === 409
          ? "That choice has changed. The table is refreshing."
          : response.status === 403
            ? "This seat could not be opened. Return to the room and use your saved table."
            : response.status === 404
              ? "This table could not be found. Check the invitation link."
              : "The table could not be reached. Please try again.",
        response.status,
      );
    return (response.headers.get("content-type")?.includes("application/json")
      ? response.json() : response.text()) as Promise<T>;
  };
  function save() {
    if (disposed || !session) return;
    store.save(session);
  }
  function stopSession() {
    tournaments?.dispose();
    tournaments = null;
    pages?.dispose();
    pages = null;
    room?.dispose();
    room = null;
    overlay?.dispose();
    overlay = null;
    stage = null;
    startOwner = startAcked = takeOwner = null;
    visitIntent = null;
    nextDelay = 0;
    replayRequest++;
    liveEpoch++;
    context.reset();
    if (timer) clearTimeout(timer);
    timer = null;
    sessionController.abort();
    sessionController = new AbortController();
    game?.dispose();
    game = null;
    bar = null;
    replay = false;
    replayUi?.dispose();
    replayUi = null;
    frames = new Map();
    timeline = null;
    shownFrame = null;
    last = null;
    resources = null;
    cursor = 0;
    reconnect = true;
    polling = false;
    sending = false;
  }
  function showError(message: string) {
    if (disposed) return;
    if (!errorElement) {
      errorElement = element("div", "bc-app-error");
      errorElement.setAttribute("role", "alert");
      root.append(errorElement);
    }
    errorElement.textContent = message;
    errorElement.hidden = false;
  }
  function currentRequest(generation: number) {
    return !disposed && generation === context.generation;
  }
  function showRequestError(
    error: unknown,
    generation: number,
    current = true,
  ) {
    if (
      current &&
      currentRequest(generation) &&
      (error as Error).name !== "AbortError"
    )
      showError((error as Error).message);
  }
  const clearError = () => {
    errorElement?.remove();
    errorElement = null;
  };
  let gameList: Promise<{ game_id: string; name: string }[]> | null = null;
  const listGames = () => (gameList ??= request<{ game_id: string; name: string }[]>("/api/games", undefined, undefined, controller.signal)
    .then(items => (Array.isArray(items) ? items : [])).catch(e => { gameList = null; throw e; }));
  /** The platform Rules window (a tab per game), the same from every page of the bar. */
  const platformRules = (selected?: string) => void listGames().then(games =>
    openRules(root, games, id => request<string>("/api/games/" + encodeURIComponent(id) + "/rules", undefined, undefined, controller.signal), selected)).catch(() => showError("The rules could not be loaded."));
  const nav = (current: Page) => {
    if (options.showNavigation !== false)
      root.append(renderNavigation({ current, go: home, exit: options.onExit, sections: options.sections, selectSection: options.selectSection, rules: () => platformRules() }));
  };
  function showTournaments(params: Record<string, string> = {}) {
    nav("tournaments");
    const page = element("div", "bc-page");
    root.append(page);
    let draft = takeDraft();
    if (!draft && params.draft) try { draft = JSON.parse(decodeURIComponent(escape(atob(params.draft.replace(/-/g, "+").replace(/_/g, "/"))))); } catch { draft = null; }
    const mounted = mountTournamentsPage(page, {
      request: (path, body, credential) => request(path, body, credential, controller.signal),
      games: () => request<any[]>("/api/games", undefined, undefined, controller.signal),
      game: params.game ?? "", selected: params.t ?? null, draft,
      setAddress: (selected, game) => {
        const query = new URLSearchParams({ ...(game ? { game } : {}), ...(selected ? { t: selected } : {}) }).toString();
        history.replaceState(null, "", `${location.pathname}${location.search}#/tournaments${query ? "?" + query : ""}`);
      },
    });
    tournaments = mounted as unknown as typeof tournaments;
    openTournament = draft => mounted.open(draft);
  }
  let openTournament: ((draft: TournamentDraft) => void) | null = null;
  let pendingDraft: TournamentDraft | null = options.tournamentDraft ?? null;
  const takeDraft = () => { const value = pendingDraft; pendingDraft = null; return value; };
  function loadHome(page: Page = "play", params: Record<string, string> = {}) {
    if (disposed) return;
    if (page === "watch") page = "play";
    if (page === "tournaments") return showTournaments(params);
    if (page === "replays" || page === "rankings") {
      nav(page);
      const section = element("div", "bc-page");
      root.append(section);
      const common = { request: <T,>(path: string) => request<T>(path, undefined, undefined, controller.signal), games: listGames, game: params.game ?? "",
        setGame: (game: string) => history.replaceState(null, "", `${location.pathname}${location.search}#/${page}${game ? `?game=${encodeURIComponent(game)}` : ""}`) };
      const mounted = page === "replays"
        ? mountReplays(section, { ...common, send: <T,>(path: string, body: unknown) => request<T>(path, body, undefined, controller.signal), openTable: (table, gameId) => { session = store.get(table) ?? { table, gameId }; backTo = { page: "replays", params: { ...(params.game ? { game: params.game } : {}) } }; openSession(true, "review"); },
          openTournament: () => home("tournaments") })
        : mountRankings(section, { request: common.request, games: listGames, game: params.game ?? "", players: params.players,
          setAddress: (game, players) => history.replaceState(null, "", `${location.pathname}${location.search}#/rankings?${new URLSearchParams({ game, players: String(players) })}`) });
      pages = mounted;
      return;
    }
    if (page === "settings") {
      nav("settings");
      const panel = element("section", "bc-platform-settings bc-panel");
      panel.append(element("h2", "", "Settings"), element("h3", "", "Colours"));
      mountPalette(panel); panel.append(element("p", "bc-small", "Saved in this browser.")); root.append(panel);
      return;
    }
    const generation = context.generation;
    room = mountRoom(root, {
      origin, store, request, fail: showError, clearError, exit: options.onExit,
      initialPage: "play",
      navigation: { sections: options.sections, selectSection: options.selectSection },
      extraModels: options.extraModels, prepareSeat: options.prepareSeat, groupFor: options.groupFor,
      preselect: page === "play" ? takePreselect() : undefined,
      showNavigation: options.showNavigation,
      // The list's page and filters and the open form live in the address (#/play?page=2&game=...&new=...).
      params,
      setAddress: (state: Record<string, string>) => {
        const query = new URLSearchParams(Object.entries(state).filter(([, v]) => v)).toString();
        history.replaceState(null, "", `${location.pathname}${location.search}#/play${query ? "?" + query : ""}`);
      },
      alive: () => currentRequest(generation),
      open: (opened, isReconnect, intent) => {
        session = opened;
        openSession(isReconnect, intent ?? null);
      },
      go: home,
    });
  }
  function home(page: Page = "play", params: Record<string, string> = {}) {
    if (disposed) return;
    stopSession();
    session = null;
    backTo = null;
    const query = new URLSearchParams(params).toString();
    // The page and its filter live in the address, so Back and a reload return to the same place.
    history.replaceState(null, "", `${location.pathname}${location.search}#/${page === "watch" ? "play" : page}${query ? "?" + query : ""}`);
    root.replaceChildren();
    clearError();
    loadHome(page, params);
  }
  /** The table's address: #/table/<id>, with ?seat=N when this browser sits there; a game's own opening view stays. */
  function tableAddress(current: Session) {
    const view = new URLSearchParams(location.hash.split("?")[1] ?? location.hash.slice(1)).get("view");
    const query = new URLSearchParams({ ...(current.seat !== undefined && current.credential ? { seat: String(current.seat) } : {}), ...(view ? { view } : {}) }).toString();
    history.replaceState(null, "", `${location.pathname}${location.search}#/table/${current.table}${query ? "?" + query : ""}`);
  }
  function openSession(isReconnect: boolean, intent: Intent | null = null) {
    if (disposed || !session) return;
    const saved = session;
    stopSession();
    session = saved;
    visitIntent = intent;
    reconnect = isReconnect;
    root.replaceChildren();
    clearError();
    tableAddress(session);
    save();
    void sync();
  }
  /** One board per table visit: it is mounted for the lobby and keeps playing, so nothing is rebuilt at the start. */
  function ensureGame(view: View, loaded: NonNullable<typeof resources>, readOnly: boolean) {
    if (game && mountedReadOnly !== readOnly) {
      // Taking a seat changes who may act, and the widget fixes that when it is mounted.
      game.dispose();
      game = null;
      stage?.remove();
      stage = null;
      overlay?.dispose();
      overlay = null;
    }
    if (game || !session) return;
    const generation = context.generation, current = session;
    stage = element("div", "bc-stage tc-screen");
    root.append(stage);
    mountedReadOnly = readOnly;
    const back = backTo;
    bar = mountGameBar({ gameId: view.config.game_id, onBack: () => (back ? home(back.page, back.params) : home()), backLabel: back?.page === "replays" ? "← Replays" : "← Play", title: tableTitle(readOnly) });
    bar.setConnection("connected");
    void loadTableNumber(current.table);
    // The bar is visible while the game view loads; the view then moves it into its own table host.
    stage.append(bar.element);
    game = mountGameView(stage, {
      gameId: view.config.game_id,
      boardSpec: loaded.spec,
      rulesText: loaded.rules,
      settings: loaded.settings,
      fullscreenRoot: stage,
      onExit: () => home(),
      gameBar: bar,
      readOnly,
      onAction: input => { if (currentRequest(generation) && current === session) void send(input); },
      onCommand: input => currentRequest(generation) && current === session
        ? sendCommand(input) : Promise.reject(Error("This table has been closed.")),
    });
  }
  /** Table numbers of this server (from the table summary), shown as "Table #N" in the game bar. */
  const tableNumbers = new Map<string, number>();
  function tableTitle(readOnly: boolean, extra = "") {
    const number = session ? tableNumbers.get(session.table) : undefined;
    return `Table ${number != null ? `#${number}` : session?.table.slice(0, 8) ?? ""}${readOnly ? " · watching" : ""}${extra}`;
  }
  /** Reads the table's number once; the bar keeps the short id until it arrives (or for an unlisted table). */
  async function loadTableNumber(table: string) {
    if (tableNumbers.has(table)) return;
    const found = await request<{ number?: number | null }>(`/api/tables/${table}/summary`).catch(() => null);
    if (found?.number == null || session?.table !== table) return;
    tableNumbers.set(table, found.number);
    bar?.setTitle(tableTitle(mountedReadOnly, replay ? " · replay" : ""));
  }
  /** Start with the creator's own credential. A stale or repeated request is re-read, never repeated. */
  async function startNow(current: Session, generation: number) {
    if ((!current.creator && !last?.can_manage) || startOwner || startAcked === current || !currentRequest(generation) || session !== current) return;
    startOwner = current;
    // The attempt is recorded before it is sent: a reload during it must not send another one by itself.
    current.autostart = false;
    current.start = "pending";
    save();
    if (last) overlay?.update(last);
    try {
      await request(`/api/tables/${current.table}/start`, {}, current.creator);
      if (!currentRequest(generation) || session !== current) return;
      startAcked = current;
      reconnect = false;
      nextDelay = 50;
    } catch (e) {
      if (!currentRequest(generation) || session !== current) return;
      const status = (e as RequestError).status;
      // A refusal (for example 409, the table already moved on) is a definite answer. A server or network
      // failure is not: the start may still have happened, so it stays pending and is only re-read.
      if (status !== undefined && status < 500) {
        delete current.start;
        save();
      }
      if (status === 409) nextDelay = 50;
      else showError(status === undefined || status >= 500
        ? "The start could not be confirmed. Boardcraft will keep checking the table; if it stays in the lobby, use Start now to ask again."
        : (e as Error).message);
      if (status !== undefined && status >= 500) nextDelay = 50;
    } finally {
      // Only the visit that began this attempt may clear it or redraw its panel.
      if (startOwner === current) startOwner = null;
      if (last && currentRequest(generation) && session === current) overlay?.update(last);
    }
  }
  function lobby(view: View, current: Session, generation: number) {
    if (!stage) return;
    overlay ??= mountLobbyOverlay(stage, {
      session: current,
      origin,
      gameId: view.config.game_id,
      gameName: gameNames.get(view.config.game_id) ?? view.config.game_id,
      table: current.table,
      storageFailed: () => store.failed,
      starting: () => startOwner === current || startAcked === current,
      taking: () => takeOwner === current,
      intent: () => visitIntent,
      take: seat => {
        // One claim per visit: a second seat would be occupied on the server with no credential kept for it.
        if (takeOwner || session !== current) return;
        takeOwner = current;
        if (last) overlay?.update(last);
        void (async () => {
          try {
            const joined = await request(`/api/tables/${current.table}/join`, { seat });
            current.credential = joined.credential;
            current.seat = seat;
            // The seat is occupied now, so its credential is kept even if the page moved on meanwhile.
            if (!disposed) store.save(current);
            if (currentRequest(generation) && session === current) tableAddress(current);
            if (currentRequest(generation) && session === current) void sync();
          } catch (e) {
            showRequestError(e, generation);
          } finally {
            if (takeOwner === current) takeOwner = null;
            if (last && currentRequest(generation) && session === current) overlay?.update(last);
          }
        })();
      },
      start: () => void startNow(current, generation),
      back: () => home(),
      setAutostart: on => {
        // An explicit choice of the creator; the server itself starts the table once every seat is ready.
        current.autostart = on;
        save();
        void request(`/api/tables/${current.table}/autostart`, { on }, current.creator)
          .catch(e => showRequestError(e, generation))
          .finally(() => { if (currentRequest(generation) && session === current) void sync(); });
      },
    });
    overlay.update(view);
  }
  /** The display name only feeds the copyable agent prompt; the game id is the fallback. */
  async function rememberGameName(id: string, signal: AbortSignal) {
    try {
      const items = await request<unknown>("/api/games", undefined, undefined, signal);
      const found = Array.isArray(items) ? items.find(item => item?.game_id === id)?.name : undefined;
      gameNames.set(id, typeof found === "string" ? found : id);
    } catch {
      if (!signal.aborted) gameNames.set(id, id);
    }
  }
  async function loadResources(gameId: string, signal: AbortSignal) {
    const path="/api/games/"+encodeURIComponent(gameId);
    const [spec,rules,settings]=await Promise.all([
      request<unknown>(path+"/board",undefined,undefined,signal),
      request<string>(path+"/rules",undefined,undefined,signal),
      request<Settings>(path+"/settings",undefined,undefined,signal),
    ]);
    return {spec,rules,settings};
  }
  async function sync() {
    if (disposed || !session || polling || sending || replay) return;
    const generation = context.generation;
    const epoch = liveEpoch;
    const currentLive = () =>
      currentRequest(generation) && epoch === liveEpoch && !replay;
    const signal = sessionController.signal;
    const current = session;
    polling = true;
    try {
      const actionMenu = () => request<ActionList>(`/api/tables/${current.table}/actions`, undefined, current.credential);
      // Only a protected menu carries a table revision. Bracket the observation
      // with that revision before publishing any seat-wide command.
      const before = current.credential ? await actionMenu() : undefined;
      if (!currentLive()) return;
      const view = await request<View>(
        `/api/tables/${current.table}/view`,
        undefined,
        current.credential,
      );
      if (!currentLive()) return;
      if(current.gameId!==view.config.game_id){current.gameId=view.config.game_id;save();}
      if (view.status === "lobby") {
        // The real board is already on screen while seats fill; no observation is invented for it.
        const loaded = resources ?? (await loadResources(view.config.game_id, signal));
        if (!currentLive()) return;
        resources = loaded;
        if (!gameNames.has(view.config.game_id)) await rememberGameName(view.config.game_id, signal);
        if (!currentLive()) return;
        ensureGame(view, loaded, !current.credential);
        last = view;
        game!.update(view, [], noActions);
        lobby(view, current, generation);
        return;
      }
      const observation = view.observation!;
      const menu = before && before.expected_revision == null ? before : await actionMenu();
      const actions: ActionList = !current.credential || observation.viewer == null
        ? { decision_id: menu.decision_id, actions: [] } : menu;
      if (!currentLive() || (before && before.expected_revision !== actions.expected_revision) ||
          !context.accept(generation, observation.revision))
        return;
      const loaded =
        resources ?? (await loadResources(view.config.game_id, signal));
      if (!currentLive()) return;
      if (actions.decision_id !== (observation.decision?.id ?? null)) return;
      const targetCursor=view.event_cursor ?? observation.data.event_cursor;
      const events: GameEvent[] = [];
      let nextCursor = cursor;
      if (reconnect) {
        nextCursor = targetCursor;
      } else {
        for (
          let page = 0;
          page < 4 && nextCursor < targetCursor;
          page++
        ) {
          const result = await request<{
            events: GameEvent[];
            next_cursor: number;
          }>(
            `/api/tables/${current.table}/events?cursor=${nextCursor}&limit=200`,
            undefined,
            current.credential,
          );
          if (!currentLive()) return;
          const permitted = result.events.filter(
            (e) => e.sequence <= targetCursor,
          );
          events.push(...permitted);
          const next = permitted.at(-1)?.sequence ?? nextCursor;
          if (next === nextCursor) break;
          nextCursor = next;
        }
      }
      if (!currentLive()) return;
      resources = loaded;
      if(nextCursor!==targetCursor)events.length=0;
      cursor=targetCursor;
      reconnect = false;
      ensureGame(view, resources, observation.viewer === null);
      overlay?.dispose();
      overlay = null;
      if (current.start) {
        // The table has left the lobby, so the unconfirmed start is settled.
        delete current.start;
        save();
      }
      game!.update(view, events, actions);
      last = view;
      bar?.setConnection("connected");
      clearError();
      if (
        view.seats?.some((seat) => seat.availability?.status === "unavailable")
      )
        showError(
          "A selected bot version is unavailable. Restore its configured source to continue.",
        );
      if (view.status === "completed") {
        // The board loads lazily; the replay bar needs it, so the review waits for the next poll if it is not there yet.
        if (visitIntent === "review" && root.querySelector(".bc-bottom")) {
          // Review from the room goes straight to the first replay position.
          visitIntent = null;
          void openReplay(0);
        }
      }
    } catch (e) {
      if(currentLive()){reconnect=true;if((e as Error).name!=="AbortError")bar?.setConnection("offline");}
      showRequestError(e, generation, currentLive());
    } finally {
      if (currentRequest(generation) && epoch === liveEpoch) polling = false;
      if (currentLive()) {
        if (timer) clearTimeout(timer);
        const delay = nextDelay || (document.hidden ? 3000 : 700);
        nextDelay = 0;
        timer = setTimeout(
          () => {
            void sync();
          },
          delay,
        );
      }
    }
  }
  async function sendCommand(input:CommandInput) {
    if(disposed || !session?.credential || sending || replay)throw Error("The table is unavailable.");
    sending=true;
    const generation=context.generation;
    liveEpoch++;
    polling=false;
    try {
      await request(`/api/tables/${session.table}/commands`,{
        expected_revision:input.expectedRevision,kind:input.kind,params:input.params,
        ...(input.decisionId ? {decision_id:input.decisionId}:{}),
      },session.credential);
    } finally {
      if(currentRequest(generation)){sending=false;await sync();}
    }
  }
  async function send(action: { decisionId: string; actionId: string }) {
    if (!session || sending || replay) return;
    sending = true;
    liveEpoch++;polling=false;
    const generation = context.generation;
    clearError();
    try {
      await request(
        `/api/tables/${session.table}/actions`,
        { decision_id: action.decisionId, action_id: action.actionId },
        session.credential,
      );
    } catch (e) {
      showRequestError(e, generation);
    } finally {
      if (generation === context.generation) {
        sending = false;
        void sync();
      }
    }
  }
  function fetchFrame(table: string, credential: string | undefined, position: number) {
    let frame = frames.get(position);
    if (!frame) {
      frame = request<ReplayFrame>(`/api/tables/${table}/replay?position=${position}`, undefined, credential);
      frames.set(position, frame);
      frame.catch(() => { if (frames.get(position) === frame) frames.delete(position); });
      if (frames.size > FRAME_CACHE) frames.delete(frames.keys().next().value as number);
    }
    return frame;
  }
  function closeReplay() {
    replayRequest++;
    liveEpoch++;
    polling = false;
    replay = false;
    replayUi?.dispose();
    replayUi = null;
    bar?.setTitle(tableTitle(mountedReadOnly));
    context.revision = -1;
    void sync();
  }
  function showFrame(frame: ReplayFrame) {
    if (!game || !last) return;
    game.update(
      {
        table_id: last.table_id, config: last.config, seats: last.seats, live: last.live,
        status: "replay", observation: frame.observation, revision: frame.observation.revision,
        event_cursor: frame.observation.data.event_cursor,
        replay_position: frame.position,
        ...(timeline ? { replay_timeline: timeline } : {}),
      } as View,
      frame.events,
      noActions,
    );
  }
  /** The whole replay at a glance (marks, net worth); an older server without it just leaves them out. */
  async function loadTimeline(current: Session, generation: number) {
    try {
      const data = await request<ReplayTimeline>(`/api/tables/${current.table}/replay/timeline`, undefined, current.credential);
      if (!replay || generation !== context.generation || session !== current || !last || !Array.isArray(data?.positions)) return;
      timeline = data;
      const { marks, legend } = timelineMarks(last.config.game_id, data, seatNames(last));
      replayUi?.setMarks(marks, legend);
      if (shownFrame) showFrame(shownFrame);
    } catch { /* optional */ }
  }
  async function openReplay(position: number, rethrow = false) {
    if (disposed || !session || !game || !last) return;
    const generation = context.generation, current = session;
    const requestId = ++replayRequest;
    liveEpoch++;
    polling = false;
    replay = true;
    if (timer) clearTimeout(timer);
    if (!replayUi) {
      replayUi = mountReplayBar({ load: n => openReplay(n, true), close: closeReplay });
      root.querySelector(".bc-bottom")?.prepend(replayUi.element);
      bar?.setExtra([]);
      bar?.setTitle(tableTitle(mountedReadOnly, " · replay"));
      if (!timeline) void loadTimeline(current, generation);
    }
    try {
      const frame = await fetchFrame(current.table, current.credential, position);
      if (disposed || generation !== context.generation || requestId !== replayRequest || !replay || !game || !last) return;
      shownFrame = frame;
      showFrame(frame);
      replayUi?.shown(frame.position, frame.total_positions, timelineText(last, frame.events));
      clearError();
      // The next position is read ahead while this one is looked at.
      if (frame.position + 1 < frame.total_positions) void fetchFrame(current.table, current.credential, frame.position + 1).catch(() => undefined);
    } catch (e) {
      showRequestError(e, generation, requestId === replayRequest && replay);
      if (rethrow) throw e;
    }
  }
  document.addEventListener(
    "visibilitychange",
    () => {
      if (!document.hidden) void sync();
    },
    { signal: controller.signal },
  );
  // A table opens from #/table/<id> (and from the older invitation form #table=<id>).
  const invited = /^#\/table\/([0-9a-f]{32})(?:\?|$)/.exec(location.hash)?.[1] ?? new URLSearchParams(location.hash.slice(1)).get("table");
  const route = /^#\/(tournaments|rankings|replays|settings|play|replay)(?:\/([0-9a-f]{32}))?(?:\?(.*))?$/.exec(location.hash);
  if (route?.[1] === "replay" && isTableId(route[2])) {
    // A replay address opens that finished table at its first position; the bar leads back to the replay list.
    session = store.tab(route[2]) ?? store.get(route[2]) ?? { table: route[2] };
    backTo = { page: "replays", params: {} };
    openSession(true, "review");
  } else if (route && route[1] !== "replay") loadHome(route[1] as Page, Object.fromEntries(new URLSearchParams(route[3] ?? "")));
  else if (isTableId(invited)) {
    // This tab's own session first, then any table this browser knows for the same API origin.
    session = store.tab(invited) ?? store.get(invited) ?? { table: invited };
    openSession(true);
  } else loadHome(options.initialPage);
  return {
    navigate: home,
    /** Opens Tournaments with the new-tournament form prefilled from a draft. */
    tournamentWith(draft: TournamentDraft) {
      if (openTournament && tournaments) return openTournament(draft);
      pendingDraft = draft;
      home("tournaments");
    },
    /** Opens Play with the new-table form prefilled; the host's own header stays as it is. */
    playWith(choice: SeatPreselect) {
      pendingPreselect = choice;
      home("play");
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      controller.abort();
      stopSession();
      root.replaceChildren();
      if (!hadAppClass) root.classList.remove("bc-app");
    },
  };
}

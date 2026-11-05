import { button, element } from "./dom";
import type { Settings } from "./play-types";
import {settingsFields,selection as seatSelection} from "./settings";
import type { MountedGameView } from "./game-view";

interface Schema {
  type?: string; title?: string; description?: string; properties?: Record<string, Schema>;
  $ref?: string; $defs?: Record<string, Schema>; enum?: unknown[]; const?: unknown;
  required?: string[]; items?: Schema; minItems?: number; minLength?: number;
  minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number;
}
interface Options { request: (path: string, body?: unknown, credential?: string) => Promise<any>; }
interface GameInfo { game_id: string; name: string; variants: string[]; min_players: number; max_players: number; rules_version: string; engine_version: string; }
type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
class GameVersionError extends Error {}

function choices(input: HTMLSelectElement, values: Array<[string, string]>, selected = "") {
  const blank = element("option", "", "Choose explicitly"); blank.value = "";
  input.replaceChildren(blank);
  for (const [value, title] of values) { const option = element("option", "", title); option.value = value; input.append(option); }
  input.value = selected;
}

/** Schema-driven controls never apply methodology defaults from the transport schema. */
function schemaForm(container: HTMLElement, schema: Schema, prefix: string, title: string, submit: (value: any) => Promise<void>, fail: (e: unknown) => void, alive: () => boolean) {
  const form = element("form", "bc-create bc-panel"), summary = element("pre"), codec = element("textarea");
  const controls = new Map<string, Control>();
  const resolve = (node: Schema): Schema => node.$ref ? { ...schema.$defs?.[node.$ref.split("/").pop()!], ...node, $ref: undefined } : node;
  summary.dataset.summary = prefix || "tournament"; codec.dataset.json = prefix || "tournament";
  codec.setAttribute("aria-label", `${prefix || "Tournament"} JSON`);
  let prepare: (value: any) => Promise<void> = async () => {};
  let changed: (path: string) => void = () => {};
  let editEpoch = 0;
  let gameSettings = () => ({} as Record<string,unknown>);
  function read(node = schema, path = ""): any {
    const result: Record<string, unknown> = {};
    for (const [key, raw] of Object.entries(resolve(node).properties ?? {})) {
      const child = resolve(raw), full = path ? `${path}.${key}` : key;
      if(full==="config.settings")continue;
      if (child.type === "object") { result[key] = read(child, full); continue; }
      const value = controls.get(full)!.value;
      if (value === "") continue;
      if (child.type === "array") result[key] = JSON.parse(value);
      else if (child.type === "integer" || child.type === "number") result[key] = Number(value);
      else result[key] = value;
    }
    if(path==="config"){const settings=gameSettings();if(Object.keys(settings).length)result.settings=settings;}
    return result;
  }
  function validate(node: Schema, value: any, path = ""): void {
    node = resolve(node);
    if (node.type === "object") {
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`Choose ${path || "all settings"}.`);
      for (const key of node.required ?? []) if (value[key] === undefined) throw new Error(`Choose ${path ? path + "." : ""}${key}.`);
      for (const [key, item] of Object.entries(value)) {
        if(path==="config" && key==="settings"){
          if(!item || typeof item!=="object" || Array.isArray(item))throw Error("Invalid game settings.");
          continue;
        }
        if (!node.properties?.[key]) throw new Error(`Unknown setting: ${key}.`);
        validate(node.properties[key], item, path ? `${path}.${key}` : key);
      }
    } else if (node.type === "array") {
      if (!Array.isArray(value) || value.length < (node.minItems ?? 0)) throw new Error(`Choose ${path}.`);
      if (node.items) value.forEach((item: unknown) => validate(node.items!, item, path));
    } else if (node.type === "integer" || node.type === "number") {
      if (typeof value !== "number" || !Number.isFinite(value) || (node.type === "integer" && !Number.isSafeInteger(value)) ||
          (node.minimum !== undefined && value < node.minimum) || (node.maximum !== undefined && value > node.maximum) ||
          (node.exclusiveMinimum !== undefined && value <= node.exclusiveMinimum) || (node.exclusiveMaximum !== undefined && value >= node.exclusiveMaximum)) throw new Error(`Invalid ${path}.`);
    } else if (node.type === "string" && (typeof value !== "string" || value.length < (node.minLength ?? 0))) throw new Error(`Choose ${path}.`);
    if (node.enum && !node.enum.includes(value) || node.const !== undefined && value !== node.const) throw new Error(`Choose ${path}.`);
  }
  function sync() { try { const json = JSON.stringify(read(), null, 2); summary.textContent = json; codec.value = json; } catch { summary.textContent = "Complete valid JSON arrays before saving."; } }
  function fields(node: Schema, path = "") {
    for (const [key, raw] of Object.entries(resolve(node).properties ?? {})) {
      const child = resolve(raw), full = path ? `${path}.${key}` : key;
      if(full==="config.settings")continue;
      if (child.type === "object") { fields(child, full); continue; }
      const label = element("label", "", child.title ?? full);
      let input: Control;
      if (child.enum || child.const !== undefined || ["config.game_id", "config.variant", "config.players"].includes(full)) {
        input = element("select"); choices(input, (child.enum ?? (child.const !== undefined ? [child.const] : [])).map(v => [String(v), String(v)]));
      } else if (child.type === "array") input = element("textarea");
      else { input = element("input"); if (child.type === "integer" || child.type === "number") { input.type = "number"; input.step = child.type === "integer" ? "1" : "any"; } }
      controls.set(full, input); input.dataset.path = prefix ? `${prefix}.${full}` : full;
      input.oninput = () => { editEpoch++; sync(); }; input.onchange = () => { editEpoch++; sync(); changed(full); };
      label.append(input, element("small", "", child.description ?? `Explicit ${full}.`)); form.append(label);
    }
  }
  form.append(element("h3", "", title)); fields(schema);
  const jsonLabel = element("label", "", "Schedule or fit settings as JSON"); jsonLabel.append(codec);
  const importer = button(`Import ${prefix || "tournament"} JSON`, () => {
    void (async () => {
      const data = JSON.parse(codec.value), epoch = ++editEpoch; validate(schema, data); await prepare(data);
      if (!alive() || epoch !== editEpoch) return;
      for (const [path, input] of controls) {
        const value = path.split(".").reduce((node, part) => node?.[part], data);
        input.value = value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
        if (value !== undefined && input.value === "") throw new Error(`Unavailable choice: ${path}.`);
      }
      sync(); changed("import");
    })().catch(fail);
  });
  const save = element("button", "", title); save.type = "submit";
  form.append(jsonLabel, importer, save, summary); container.append(form); sync();
  form.onsubmit = event => { event.preventDefault(); if (!alive()) return; try { const value = read(); validate(schema, value); void submit(value).catch(fail); } catch (e) { fail(e); } };
  return { controls, sync, read, setGameSettings(get: typeof gameSettings){gameSettings=get;}, setBusy(busy: boolean) { save.disabled = busy; }, setChanged(fn: typeof changed) { changed = fn; }, setPrepare(fn: typeof prepare) { prepare = fn; } };
}

/** Public gameplay only: capabilities live in memory, reads never dispatch work. */
export function mountTournaments(root: HTMLElement, options: Options) {
  const hadClass = root.classList.contains("bc-tournaments"); root.classList.add("bc-tournaments");
  let disposed = false, selected: any = null, game: MountedGameView | null = null;
  let selection = 0, replayEpoch = 0, listEpoch = 0, catalogEpoch = 0;
  const owned = new Map<string, string>();
  const status = element("p", "", "Loading tournament controls…"); status.setAttribute("role", "status");
  const createRoot = element("div"), fitRoot = element("div"), listing = element("div"), details = element("pre"), replayRoot = element("div"), ratingsRoot = element("div");
  const id = element("input"), credential = element("input"), controls = element("div", "bc-replay");
  id.setAttribute("aria-label", "Tournament ID"); credential.setAttribute("aria-label", "Creator credential"); credential.type = "password"; credential.autocomplete = "off";
  const idLabel = element("label", "", "Tournament ID"), credentialLabel = element("label", "", "Creator credential"); idLabel.append(id); credentialLabel.append(credential);
  root.replaceChildren(element("h2", "", "Tournaments and ratings"), status, createRoot, listing, idLabel, credentialLabel, controls, details, ratingsRoot, fitRoot, replayRoot);
  function error(_e: unknown) { if (!disposed) status.textContent = _e instanceof GameVersionError ? _e.message : "The tournament request could not be completed. Check your explicit settings, saved revision and creator credential, then refresh and retry."; }
  const current = (epoch: number, tournamentId: string) => !disposed && epoch === selection && id.value === tournamentId;
  const selectionError = (epoch = selection, tournamentId = id.value) => (e: unknown) => { if (current(epoch, tournamentId)) error(e); };
  function clearReplay() { replayEpoch++; game?.dispose(); game = null; replayRoot.replaceChildren(); }
  id.oninput = () => { selection++; selected = null; credential.value = owned.get(id.value) ?? ""; details.textContent = ""; ratingsRoot.replaceChildren(); clearReplay(); };
  function present(value: any) {
    selected = value; details.textContent = JSON.stringify(value, null, 2);
    status.textContent = `${value.status}. Read saved tournament to refresh progress. Stop drains games and record publication before restart or delete.`;
    clearReplay();
    for (const match of value.matches ?? []) if (match.status === "completed") {
      replayRoot.append(button(`Replay match ${match.ordinal + 1}`, () => { void showReplay(value, match); }));
    }
  }
  async function read() {
    const tournamentId = id.value, epoch = ++selection; clearReplay();
    const value = await options.request(`/api/tournaments/${encodeURIComponent(tournamentId)}`).catch(selectionError(epoch, tournamentId));
    if (!value || !current(epoch, tournamentId)) return;
    credential.value = owned.get(tournamentId) ?? credential.value; ratingsRoot.replaceChildren(); present(value);
  }
  async function refresh() {
    const epoch = ++listEpoch, values = await options.request("/api/tournaments");
    if (disposed || epoch !== listEpoch) return;
    listing.replaceChildren(element("h3", "", "Saved tournaments"));
    if (!values.length) listing.append(element("p", "", "No saved tournaments."));
    for (const item of values) listing.append(button(`${item.spec.name}: ${item.status}`, () => { id.value = item.tournament_id; credential.value = owned.get(id.value) ?? ""; void read().catch(selectionError()); }));
  }
  async function showReplay(value: any, match: any) {
    const tournamentId = value.tournament_id, epoch = selection, requestEpoch = ++replayEpoch;
    const gameId = value.spec.config.game_id, gamePath = encodeURIComponent(gameId);
    const replayCurrent = () => current(epoch, tournamentId) && requestEpoch === replayEpoch;
    try {
      const [boardSpec, rulesText, settings, frame, widget] = await Promise.all([
        options.request(`/api/games/${gamePath}/board`), options.request(`/api/games/${gamePath}/rules`), options.request(`/api/games/${gamePath}/settings`),
        options.request(`/api/tournaments/${encodeURIComponent(tournamentId)}/matches/${encodeURIComponent(match.match_id)}/replay?position=0`), import("./game-view"),
      ]);
      if (!replayCurrent()) return;
      game?.dispose(); const bar = element("div", "bc-replay"), holder = element("div", "bc-embedded-board");
      const stage = element("section", "bc-embedded-stage"); stage.append(bar, holder); replayRoot.replaceChildren(stage);
      game = widget.mountGameView(holder, { gameId, boardSpec, rulesText, settings, readOnly: true, fullscreenRoot: stage, onExit:()=>{if(replayCurrent())present(value);} });
      let position = 0, frameEpoch = 0;
      const prev = button("Previous", () => { void seek(position - 1); });
      const next = button("Next", () => { void seek(position + 1); }); const progress = element("span"); bar.append(prev, progress, next);
      const update = (frame: any) => {
        position = frame.position; prev.disabled = position <= 0; next.disabled = position >= frame.total_positions - 1; progress.textContent = `Replay ${position + 1}/${frame.total_positions}`;
        game!.update({ table_id: tournamentId, status: "replay", config: value.spec.config, observation: frame.observation,
          seats: (match.result?.identities ?? []).map((identity: string, seat: number) => ({ seat, kind: "bot", display_name: identity, occupied: true })) }, frame.events, { table_id: tournamentId, decision_id: null, actions: [] });
      };
      async function seek(target: number) {
        const frameRequest = ++frameEpoch;
        const frameCurrent = () => replayCurrent() && frameRequest === frameEpoch;
        try {
          const frame = await options.request(`/api/tournaments/${encodeURIComponent(tournamentId)}/matches/${encodeURIComponent(match.match_id)}/replay?position=${target}`);
          if (frameCurrent()) update(frame);
        } catch (e) { if (frameCurrent()) error(e); }
      }
      update(frame);
    } catch (e) { if (replayCurrent()) error(e); }
  }
  function savedSelection() {
    if (!selected || selected.tournament_id !== id.value) throw new Error("Read the exact saved tournament first.");
    return { tournamentId: id.value, epoch: selection, revision: selected.revision, spec_sha: selected.spec_sha };
  }
  controls.append(button("Read saved tournament", () => { void read().catch(selectionError()); }), button("Refresh list", () => { void refresh().catch(selectionError()); }));
  for (const action of ["start", "stop", "restart", "delete"]) controls.append(button(action[0].toUpperCase() + action.slice(1), () => {
    void (async () => {
      const pin = savedSelection(); if (!credential.value) throw new Error("Creator credential required.");
      const body: any = { revision: pin.revision }; if (action === "start" || action === "restart") body.spec_sha = pin.spec_sha;
      const value = await options.request(`/api/tournaments/${encodeURIComponent(pin.tournamentId)}/${action}`, body, credential.value);
      if (current(pin.epoch, pin.tournamentId) && value?.tournament_id === pin.tournamentId &&
          Number.isSafeInteger(value.revision) && value.revision >= pin.revision && typeof value.status === "string") {
        present(value);
        if (action === "delete" && value.status === "deleted" && value.cleanup_unconfirmed === false) {
          owned.delete(pin.tournamentId); credential.value = "";
        }
      }
    })().catch(selectionError());
  }));
  function ratings(value: any) {
    ratingsRoot.replaceChildren(element("h3", "", "Saved ratings"));
    const tables = Array.isArray(value) ? value : [value];
    if (!tables.length) ratingsRoot.append(element("p", "", "No fitted rating snapshot. Fit completed results explicitly to create one."));
    for (const table of tables) {
      ratingsRoot.append(element("p", "", table.key?.players === 1 ? "Solo: points and counts only; no Elo strength." : "Components are separate. Cross-component order is unidentified; null estimates are unavailable. Inspect convergence, finiteness and separation before interpreting estimates."), element("pre", "", JSON.stringify(table, null, 2)));
    }
  }
  controls.append(button("Read saved ratings", () => {
    void (async () => { const pin = savedSelection(), value = await options.request(`/api/tournaments/${encodeURIComponent(pin.tournamentId)}/ratings`); if (value && current(pin.epoch, pin.tournamentId)) ratings(value); })().catch(selectionError());
  }));
  void Promise.all([options.request("/api/tournaments/schema"), options.request("/api/games")]).then(([schemas, games]: [any, GameInfo[]]) => {
    if (disposed) return;
    let creating = false;
    const create = schemaForm(createRoot, schemas.tournament, "", "Save tournament", async body => {
      if (creating) return;
      creating = true; create.setBusy(true);
      const epoch = selection;
      try {
        const made = await options.request("/api/tournaments", body).catch(selectionError(epoch));
        if (!made || disposed) return;
        // Ownership survives display/selection changes; credentials remain in memory only.
        owned.set(made.tournament_id, made.creator_credential);
        if (epoch !== selection) return;
        id.value = made.tournament_id; credential.value = made.creator_credential; await read(); await refresh();
      } finally {
        creating = false;
        if (!disposed) create.setBusy(false);
      }
    }, error, () => !disposed);
    const gameChoice = create.controls.get("config.game_id") as HTMLSelectElement;
    const variant = create.controls.get("config.variant") as HTMLSelectElement;
    const players = create.controls.get("config.players") as HTMLSelectElement;
    const rulesVersion = create.controls.get("rules_version") as HTMLInputElement;
    const engineVersion = create.controls.get("engine_version") as HTMLInputElement;
    for (const control of [rulesVersion, engineVersion]) { control.readOnly = true; control.closest("label")!.querySelector("small")!.append(" Set by your game selection."); }
    const seats = create.controls.get("seats")!; const seatChoices = element("div", "bc-seat-setup"); seats.closest("label")!.append(seatChoices);
    const metadata = element("p"); createRoot.append(metadata);
    let bots: Array<{ choice_id: string; display_name: string }> = [];
    choices(gameChoice, games.map(info => [info.game_id, info.name]));
    const moduleRoot=element("div");
    gameChoice.closest("label")!.after(moduleRoot);
    let settings:Settings|undefined;
    let configured:ReturnType<typeof settingsFields>|undefined;
    create.setGameSettings(()=>configured?.read() ?? {});
    let commitSeats = () => {};
    function setupSeats() {
      commitSeats = () => {};
      type Seat = string | {choice_id:string;options:Record<string,unknown>};
      let selected: Seat[] = []; try { selected = JSON.parse(seats.value || "[]"); } catch {}
      seatChoices.replaceChildren();
      const count = Number(players.value);
      if (!Number.isSafeInteger(count) || count < 1) return;
      const readers:Array<()=>Seat>=[];
      commitSeats = () => {
        const values = readers.map(read => read());
        seats.value = values.every(Boolean) ? JSON.stringify(values) : "";
      };
      const commit = () => {
        try { commitSeats(); }
        catch { seats.value = ""; }
        create.sync();
      };
      for (let i = 0; i < count; i++) {
        const label = element("label", "", `Seat ${i + 1}`), input = element("select"); input.dataset.seat = String(i);
        const prior=selected[i], chosen=typeof prior==="object"?prior.choice_id:prior ?? "";
        choices(input, bots.map(bot => [bot.choice_id, bot.display_name]),chosen);
        const params=element("div");
        const draw=(saved={})=>{params.replaceChildren();return settingsFields(params,"Bot settings",settings?.bots?.[input.value],{},saved,commit);};
        let fields=draw(typeof prior==="object"?prior.options:{});
        input.onchange=()=>{fields=draw();commit();};
        readers.push(()=>seatSelection(input.value,fields.read()));
        label.append(input);seatChoices.append(label,params);
      }
    }
    async function discover(gameId: string, saved:Record<string,unknown>={}) {
      const epoch = ++catalogEpoch, selectionEpoch = selection, tournamentId = id.value;
      const catalogCurrent = () => current(selectionEpoch, tournamentId) && epoch === catalogEpoch && gameChoice.value === gameId;
      commitSeats = () => {};
      settings=undefined;configured=undefined;moduleRoot.replaceChildren();
      bots = []; choices(variant, []); choices(players, []); seatChoices.replaceChildren(); metadata.textContent = ""; rulesVersion.value = ""; engineVersion.value = ""; seats.value = ""; create.sync();
      const info = games.find(info => info.game_id === gameId); if (!info) return;
      if (!info.rules_version || !info.engine_version) throw new GameVersionError("This game has no exact version information. Refresh the available games before creating a tournament.");
      rulesVersion.value = info.rules_version; engineVersion.value = info.engine_version;
      choices(variant, info.variants.map(value => [value, value]));
      choices(players, Array.from({ length: info.max_players - info.min_players + 1 }, (_, i) => { const value = String(info.min_players + i); return [value, value]; }));
      metadata.textContent = "Game versions are linked to your chosen game and saved with the schedule."; create.sync();
      const path="/api/games/"+encodeURIComponent(gameId);
      const result = await Promise.all([options.request(path+"/bots"),options.request(path+"/settings")])
        .catch(e=>{if(catalogCurrent())throw e;});
      if(!result || !catalogCurrent())return;
      [bots,settings]=result;
      configured=settingsFields(moduleRoot,"Simulation settings",settings?.table,settings?.simulation_defaults,saved,create.sync);
      setupSeats();create.sync();
    }
    function checkVersions(value: any) {
      const info = games.find(info => info.game_id === value.config?.game_id);
      if (!info || !info.rules_version || !info.engine_version) throw new GameVersionError("Choose a listed game with exact version information.");
      if (value.rules_version !== info.rules_version || value.engine_version !== info.engine_version) throw new GameVersionError("The schedule does not match the chosen game versions. Choose a matching game or import a schedule with the listed versions.");
    }
    create.setPrepare(async value => { checkVersions(value); gameChoice.value = value.config.game_id; await discover(value.config.game_id,value.config.settings); });
    create.setChanged(path => {
      if (path === "config.game_id") { seats.value = ""; void discover(gameChoice.value).then(() => { if (!disposed) create.sync(); }).catch(selectionError()); }
      else if (path === "config.players") { seats.value = ""; setupSeats(); create.sync(); }
      else if (path === "seats" || path === "import") setupSeats();
    });
    const originalSubmit = createRoot.querySelector("form")!.onsubmit!;
    createRoot.querySelector("form")!.onsubmit = event => {
      try {
        commitSeats();
        const data = create.read(), info = games.find(info => info.game_id === data.config.game_id);
        checkVersions(data);
        if (!info || !info.variants.includes(data.config.variant) || data.config.players < info.min_players || data.config.players > info.max_players || !Array.isArray(data.seats) || data.seats.length !== data.config.players || data.seats.some((seat:string|{choice_id:string}) => !bots.some(bot => bot.choice_id === (typeof seat==="string"?seat:seat.choice_id)))) throw new Error("Choose available game and seats.");
        originalSubmit.call(createRoot.querySelector("form")!, event);
      } catch (e) { event.preventDefault(); error(e); }
    };
    schemaForm(fitRoot, schemas.fit, "fit", "Fit completed results", async fit => {
      const pin = savedSelection(); if (!credential.value) throw new Error("Creator credential required.");
      const value = await options.request(`/api/tournaments/${encodeURIComponent(pin.tournamentId)}/fit`, { revision: pin.revision, fit }, credential.value).catch(selectionError(pin.epoch, pin.tournamentId));
      if (value && current(pin.epoch, pin.tournamentId)) ratings(value);
    }, error, () => !disposed);
    status.textContent = "Choose and save an explicit schedule, then start it separately. Rating fit settings are optional for play. Creator credentials are held only while this panel remains open.";
    void refresh().catch(selectionError());
  }).catch(selectionError());
  return { dispose() { disposed = true; selection++; catalogEpoch++; clearReplay(); owned.clear(); credential.value = ""; root.replaceChildren(); if (!hadClass) root.classList.remove("bc-tournaments"); } };
}

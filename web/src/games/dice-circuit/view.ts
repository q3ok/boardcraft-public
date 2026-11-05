import { emblemIcon } from "../../emblem";
import { SharedTable3D } from "../../scene/table";
import { DiceCircuitScene, PAPER_FRAME_H, PAPER_FRAME_W } from "../../scene/dice-circuit";
import { LegalSelection, actionContext, seatLabel, seatNames, windowEntries, type WindowEntry } from "../../model";
import { describeDiceEvent } from "./describe";
import { bonusName } from "../../scene/sheet";
import {
  DICE,
  AREAS,
  COLORS,
  type DieId,
  type Action,
  type View,
  type BoardSpec,
  type ActionList,
  type GameEvent,
} from "./types";
import { button, element } from "../../dom";
import type { GameViewOptions } from "../../game-view";
import { confirmDialog, mountGameBar, rulesDialog } from "../../ui/gamebar";
export function mountDiceCircuitView(
  root: HTMLElement,
  options: GameViewOptions & { boardSpec: BoardSpec },
) {
  const host = element("section", "bc-game tc-screen dice-stage");
  root.append(host);
  const fullscreenRoot = options.fullscreenRoot ?? host;
  const canvas = element("canvas", "bc-canvas");
  canvas.setAttribute(
    "aria-label",
    "Dice Circuit three-dimensional table. Use the dice and field buttons for keyboard play.",
  );
  host.append(canvas);
  // The shared game bar (back, mark, table, connection, Rules); the host may own it, an embedded view makes its own.
  const bar = options.gameBar ?? mountGameBar({ gameId: "dice_circuit", onBack: options.onExit, backLabel: options.onExit ? "← Back" : undefined });
  const nav = bar.element;
  host.append(nav);
  const status = element("div", "bc-status");
  status.setAttribute("role", "status");
  host.append(status);
  const scores = element("div", "bc-scores bc-panel");
  host.append(scores);
  // Always next to the sheet: the resources of the seat this screen plays (or the solo seat), then the recent moves.
  const sidePanel = element("aside", "bc-side");
  sidePanel.setAttribute("aria-label", "Resources and recent moves");
  const resources = element("div", "bc-resources bc-panel");
  resources.setAttribute("aria-live", "polite");
  const movesPanel = element("div", "bc-moves bc-panel");
  const movesList = element("ol", "bc-moves-list");
  movesPanel.append(element("h4", "", "Recent moves"), movesList);
  // In a replay with the server's timeline: the decision at this position and the model's recorded reasoning.
  const mindPanel = element("div", "bc-mind bc-panel");
  const mindBox = element("div");
  mindPanel.append(element("h4", "", "In the player's head"), mindBox);
  mindPanel.hidden = true;
  sidePanel.append(resources, mindPanel, movesPanel);
  host.append(sidePanel);
  let moves: string[] = [];
  let openedView = false;
  const bottom = element("div", "bc-bottom");
  const cameras = element("nav", "bc-cameras bc-panel");
  cameras.setAttribute("aria-label", "Table cameras");
  bottom.append(cameras);
  const interaction = element("section", "bc-interaction bc-panel");
  interaction.setAttribute("aria-label", "Your decision");
  bottom.append(interaction);
  host.append(bottom);
  const message = element("div", "bc-failure bc-panel");
  message.setAttribute("role", "alert");
  message.hidden = true;
  host.append(message);
  const selection = new LegalSelection();
  let latest: View | null = null,
    latestActions: ActionList = { decision_id: null, actions: [] };
  let latestRoster = "";
  let disposed = false;
  let camera = "table";
  let pending = false;
  let areaFilter: string | null = null;
  let table: SharedTable3D | null = null;
  let scene: DiceCircuitScene | null = null;
  const controls = new AbortController();
  const refreshSelection = () => {
    if (!latest) return;
    scene?.update(latest, [], selection.actions);
    renderInteraction();
  };
  const choose = (action: Action) => {
    if (
      pending ||
      !selection.decisionId ||
      !selection.actions.some((a) => a.id === action.id) ||
      options.readOnly
    )
      return;
    pending = true;
    renderInteraction();
    options.onAction?.({
      decisionId: selection.decisionId,
      actionId: action.id,
    });
  };
  const selectDie = (id: DieId) => {
    selection.selectDie(id);
    areaFilter = null;
    refreshSelection();
  };
  try {
    table = new SharedTable3D(canvas, host, (text) => {
      message.textContent = text;
      message.hidden = false;
    });
    scene = new DiceCircuitScene(
      options.boardSpec,
      selection,
      selectDie,
      choose,
    );
    scene.mount(table);
  } catch {
    message.textContent =
      "This device could not open the 3D table. Enable WebGL in your browser, then reload to return to this game.";
    message.hidden = false;
    canvas.hidden = true;
  }
  /** A narrow window or a full table can put the score boxes under the game bar: they then move below it. */
  function unstack() {
    scores.style.top = status.style.top = sidePanel.style.top = "";
    const below = (el: HTMLElement) => el.offsetTop + el.offsetHeight;
    const overlaps = (a: HTMLElement, b: HTMLElement) => a.offsetHeight > 0 && b.offsetHeight > 0 &&
      a.offsetLeft < b.offsetLeft + b.offsetWidth && b.offsetLeft < a.offsetLeft + a.offsetWidth &&
      a.offsetTop < below(b) && b.offsetTop < below(a);
    if (overlaps(scores, nav)) scores.style.top = `${below(nav) + 8}px`;
    if (overlaps(status, scores)) status.style.top = `${below(scores) + 8}px`;
    if (overlaps(sidePanel, scores)) sidePanel.style.top = `${below(scores) + 12}px`;
    if (overlaps(sidePanel, status)) sidePanel.style.top = `${below(status) + 12}px`;
  }
  function insets() {
    unstack();
    if (!table) return;
    const sheetView = host.classList.contains("bc-sheet-view");
    const side = sheetView || host.clientWidth < 700 ? 12 : 24;
    const bottomInset =
      bottom.offsetHeight + bottom.offsetTop > host.clientHeight
        ? bottom.offsetHeight + 24
        : host.clientHeight - bottom.offsetTop + 16;
    let top = Math.max(
      nav.offsetTop + nav.offsetHeight + 20,
      scores.offsetTop + scores.offsetHeight + 12,
      status.offsetTop + status.offsetHeight + 15,
    );
    if (sheetView) {
      // The paper is centred and narrower than a wide window, so only the controls that reach over its width push it down.
      const paperW = Math.min(
        host.clientWidth - 2 * side,
        ((host.clientHeight - 12 - bottomInset) * PAPER_FRAME_W) / PAPER_FRAME_H,
      );
      const from = (host.clientWidth - paperW) / 2;
      top = 12;
      for (const [el, gap] of [[nav, 20], [scores, 12], [status, 15]] as const)
        if (el.offsetHeight && el.offsetLeft < from + paperW && el.offsetLeft + el.offsetWidth > from)
          top = Math.max(top, el.offsetTop + el.offsetHeight + gap);
    }
    // The side column (resources, the player's head, recent moves) stops above the bottom controls, so a low window
    // never puts the moves under the replay or decision panel; its moves list shrinks and scrolls first.
    const room = bottom.offsetTop - sidePanel.offsetTop - 12;
    sidePanel.style.maxHeight = room > 0 ? `${Math.max(room, 64)}px` : "";
    // The resources and recent moves sit at the right of a wide screen, so the table is framed beside them.
    const column = host.clientWidth >= 700 && sidePanel.offsetWidth ? sidePanel.offsetWidth + 24 + side : side;
    table.setInsets({ top, bottom: bottomInset, left: side, right: column });
  }
  const resize = new ResizeObserver(insets);
  resize.observe(bottom);
  resize.observe(scores);
  resize.observe(nav);
  resize.observe(sidePanel);
  resize.observe(host); // a turned phone or a resized window moves the controls the paper must stay clear of
  const cameraButtons = new Map<string, HTMLButtonElement>();
  for (const [name, label] of [
    ["table", "Table"],
    ["top", "Top"],
    ["sheet", "My sheet"],
    ["opponents", "Opponents"],
  ]) {
    const b = button(label, () => {
      camera = name;
      scene?.setCamera(name);
      for (const [id, btn] of cameraButtons)
        btn.setAttribute("aria-pressed", String(id === name));
      host.classList.toggle(
        "bc-sheet-view",
        name === "sheet" || name === "opponents",
      );
      insets();
    });
    b.setAttribute("aria-pressed", String(name === camera));
    cameras.append(b);
    cameraButtons.set(name, b);
  }
  const full = button("Full screen", () => {
    if (document.fullscreenElement)
      void document.exitFullscreen().catch(() => {});
    else
      void fullscreenRoot.requestFullscreen?.().catch(() => {
        message.textContent = "Full screen is unavailable in this browser.";
        message.hidden = false;
      });
  });
  cameras.append(full);
  document.addEventListener(
    "fullscreenchange",
    () => {
      full.textContent = document.fullscreenElement
        ? "Exit full screen"
        : "Full screen";
      table?.resize();
    },
    { signal: controls.signal },
  );
  bar.setRules(() => rulesDialog(host, "Dice Circuit", options.rulesText));
  function actionLabel(a: Action) {
    if (a.kind === "choose_bonus") {
      const stack = latest?.observation?.data.window?.bonus_stack;
      const pending = stack?.frames.flatMap((f) => f.pending) ?? [];
      const item = pending.find((p) => a.params.bonus_id === p.source_id);
      return `Resolve ${bonusName(item?.kind ?? "bonus")}`;
    }
    if (a.kind === "choose_round_bonus")
      return `Choose ${bonusName(a.params.bonus)}`;
    if (a.kind === "finish_extra") {
      const left = latest?.observation ? latest.observation.data.sheets[latest.observation.viewer ?? 0]?.extras ?? 0 : 0;
      return `Finish - keep ${left} +1 for later`;
    }
    if (a.kind === "reroll") return "↻ Reroll (spends 1 reroll)";
    return a.label;
  }
  const fieldText = (p: Record<string, any>) =>
    p.area === "yellow" ? `yellow row ${p.target[0] + 1}, column ${p.target[1] + 1}`
      : p.area === "blue" ? `blue ${p.target}` : `${p.area} field ${p.target + 1}`;
  /** Every die at a glance, in every camera: colour, value and where it lies; usable dice can be picked from here. */
  function diceStrip(o: NonNullable<View["observation"]>, actions: Action[]) {
    const row = element("div", "bc-dice-row");
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", "Dice");
    const zone: Record<string, string> = { available: "rolled", kept: "kept", tray: "tray" };
    for (const [i, id] of DICE.entries()) {
      const die = o.data.dice[id];
      const enabled = actions.some((a) => a.params.die_id === id);
      const b = button("", () => selectDie(id), `bc-die-button bc-zone-${die.zone}`);
      b.append(element("strong", "", `${id[0].toUpperCase() + id.slice(1)} ${die.value}`), element("small", "", zone[die.zone] ?? die.zone));
      b.style.setProperty("--die-color", COLORS[id]);
      b.disabled = !enabled || pending;
      b.setAttribute("aria-pressed", String(selection.selectedDie === id));
      b.setAttribute("aria-label", `${i + 1}. ${id} die, ${die.value}, ${zone[die.zone] ?? die.zone}${enabled ? ", available to use" : ""}`);
      row.append(b);
    }
    return row;
  }
  function renderInteraction() {
    interaction.replaceChildren();
    const o = latest?.observation;
    if (!o) return;
    if (options.readOnly || latest?.status === "replay" || o.viewer === null) {
      interaction.append(
        element(
          "p",
          "bc-decision-title",
          latest?.status === "replay"
            ? "Reviewing the game"
            : o.data.phase === "over"
              ? "Game finished"
              : "Watching the table",
        ),
      );
      interaction.append(diceStrip(o, []));
      return;
    }
    const actions = selection.actions;
    const bonus = o.data.window?.bonus_stack;
    const solo = o.data.sheets.length === 1;
    const stage = o.data.window?.stage;
    const names = seatNames(latest!);
    const extrasLeft = o.data.sheets[o.viewer]?.extras ?? 0;
    const usable = actions.filter((a) => a.params.die_id);
    const fromKept = usable.length > 0 && usable.every((a) => o.data.dice[a.params.die_id as DieId]?.zone === "kept");
    const passive = stage === "passive" && usable.length > 0;
    const extra = actions.some((a) => a.kind === "extra_die");
    const title = actions.some((a) => a.kind === "choose_round_bonus")
      ? `Round ${o.data.round} · Choose your bonus`
      : actions.some((a) => a.kind === "choose_bonus")
        ? "Choose the next bonus branch"
        : actions.some((a) => a.kind === "place_bonus")
          ? `Place ${bonusName(bonus?.current?.kind ?? "bonus")}`
          : extra
            ? `Now you may use +1: pick any die or finish (${extrasLeft} +1 left)`
            : passive
              ? solo
                ? "Simulated opponent's turn: pick 1 of the 3 dice on the tray, then a field"
                : `${names[o.data.active_player]}'s turn: pick 1 of the dice on their tray, then a field`
              : actions.length
                ? "Your turn: choose a die, then a field"
                : o.data.phase === "over"
                  ? "Game finished"
                  : "Waiting for the other players";
    interaction.append(
      element(
        "p",
        "bc-decision-title",
        pending ? "Sending your choice…" : title,
      ),
    );
    if (!pending && passive && fromKept)
      interaction.append(element("p", "bc-hint bc-exception", solo
        ? "None of the 3 tray dice fits your sheet, so you may use one of the other three dice of this roll (no +1 cost)."
        : "None of the tray dice fits your sheet, so you may use a die kept on the active player's sheet (no +1 cost)."));
    if (!pending && extra)
      interaction.append(element("p", "bc-hint", "+1 comes after your regular choice: each +1 spends one resource for one more entry, each physical die once."));
    interaction.append(diceStrip(o, actions));
    const commandRow = element("div", "bc-command-row");
    for (const a of actions.filter((a) => !a.params.area)) {
      const b = button(
        actionLabel(a),
        () => choose(a),
        a.kind === "reroll" ? "bc-primary" : "",
      );
      b.disabled = pending;
      commandRow.append(b);
    }
    if (commandRow.childElementCount) interaction.append(commandRow);
    if (
      selection.selectedDie === "white" ||
      actions.some((a) => a.kind === "place_bonus")
    ) {
      const areas = AREAS.filter((area) =>
        selection.targets().some((a) => a.params.area === area),
      );
      if (areas.length > 1) {
        const filters = element("div", "bc-area-filters");
        for (const area of areas) {
          const b = button(area[0].toUpperCase() + area.slice(1), () => {
            areaFilter = areaFilter === area ? null : area;
            renderInteraction();
          });
          b.setAttribute("aria-pressed", String(area === areaFilter));
          filters.append(b);
        }
        interaction.append(filters);
      }
    }
    const targets = selection
      .targets()
      .filter((a) => !areaFilter || a.params.area === areaFilter);
    if (targets.length) {
      const row = element("div", "bc-targets");
      row.setAttribute("aria-label", "Legal fields");
      for (const a of targets) {
        const p = a.params;
        const o2 = latest!.observation!;
        const left = o2.data.sheets[o2.viewer ?? 0]?.extras ?? 0;
        const label = a.kind === "extra_die"
          ? `Use +1: ${p.die_id} ${o2.data.dice[p.die_id as DieId]?.value} -> ${fieldText(p)} (${left} left -> ${left - 1})`
          : p.area === "yellow"
            ? `Yellow · row ${p.target[0] + 1}, col ${p.target[1] + 1}`
            : p.area === "blue"
              ? `Blue · ${p.target}`
              : `${p.area[0].toUpperCase() + p.area.slice(1)} · field ${p.target + 1}`;
        const b = button(label, () => choose(a), "bc-target");
        b.style.setProperty("--area-color", COLORS[p.area as DieId]);
        b.disabled = pending;
        row.append(b);
      }
      interaction.append(row);
    } else if (actions.some((a) => a.params.die_id))
      interaction.append(
        element(
          "p",
          "bc-hint",
          "Choose an enabled die above or press 1-6. Lit fields on your paper are legal.",
        ),
      );
    if (bonus && bonus.frames.length) {
      const chain = element("div", "bc-chain");
      const names = bonus.frames.flatMap((f) =>
        f.pending.map((p) => bonusName(p.kind)),
      );
      chain.textContent = `Bonus chain${bonus.current ? `: ${bonusName(bonus.current.kind)}` : ""}${names.length ? ` · Next: ${names.join(", ")}` : ""}`;
      interaction.append(chain);
    }
    insets();
  }
  /** One unambiguous header: the round of the game and whose turn it is. */
  /** The game bar's Surrender: offered to a seated person while the server lists the seat's resign command. */
  function renderSurrender(view: View, actions: ActionList) {
    const resign = actions.commands?.find((c) => c.kind === "resign");
    const revision = actions.expected_revision;
    const playing = !options.readOnly && view.status !== "replay" && view.observation?.viewer != null && !view.observation.data.result;
    bar.setSurrender(playing && resign && options.onCommand && revision != null ? () => confirmDialog(host, "Surrender the game?",
      "You leave the game; the table ends and the final sheets are shown. This cannot be undone.",
      "🏳 Yes, I surrender", () => void options.onCommand!({ kind: "resign", params: {}, expectedRevision: revision }).catch((e: Error) => {
        message.textContent = e.message;
        message.hidden = false;
      })) : null);
  }
  function statusText(view: View) {
    const o = view.observation!, d = o.data, names = seatNames(view);
    if (d.result) return `Game complete · ${d.result.scores.map((sc, i) => `${seatLabel(view, i)}: ${sc} points`).join(" · ")}`;
    const total = d.rounds_total ?? 0;
    const round = `Round ${d.round}${total ? ` of ${total}` : ""}`;
    const active = d.active_player, mine = o.viewer === active;
    const turn = d.turn_phase === "round_bonus" ? "everyone chooses a round bonus"
      : d.turn_phase === "passive"
        ? d.sheets.length === 1 ? "simulated opponent's turn"
          : mine ? "the others choose from your tray" : o.viewer === null ? `everyone else chooses from ${names[active]}'s tray` : `choose from ${names[active]}'s tray`
        : d.turn_phase === "active_extra" ? `${mine ? "your" : `${names[active]}'s`} turn · +1 choices`
          : `${mine ? "your" : `${names[active]}'s`} turn · roll ${d.regular_opportunity} of 3`;
    return `${round}: ${turn}${d.phase === "bonus" ? " · bonus chain" : ""}`;
  }
  function renderScores() {
    scores.replaceChildren();
    const o = latest?.observation;
    if (!o) {
      // Lobby: neutral seat chips that only move the camera between the blank sheets.
      for (let seat = 0; seat < (latest?.config.players ?? 0); seat++) {
        const info = latest?.seats?.find((s) => s.seat === seat);
        const b = button("", () => {
          camera = seat === 0 ? "sheet" : "opponents";
          if (seat === 0) scene?.setCamera("sheet");
          else scene?.setOpponent(seat);
          for (const [id, btn] of cameraButtons)
            btn.setAttribute("aria-pressed", String(id === camera));
          host.classList.add("bc-sheet-view");
          insets();
        }, "bc-score");
        b.append(
          element("span", "", seatNames(latest!)[seat] ?? `Player ${seat + 1}`),
          element("strong", "", info?.occupied && info.availability?.status !== "unavailable" ? "Ready" : "Open"),
        );
        scores.append(b);
      }
      return;
    }
    for (let seat = 0; seat < o.data.sheets.length; seat++) {
      const b = button(
        "",
        () => {
          if (seat === o.viewer || (o.viewer === null && seat === 0)) {
            camera = "sheet";
            scene?.setCamera("sheet");
          } else {
            camera = "opponents";
            scene?.setOpponent(seat);
          }
          for (const [id, btn] of cameraButtons)
            btn.setAttribute("aria-pressed", String(id === camera));
          host.classList.add("bc-sheet-view");
          insets();
        },
        "bc-score",
      );
      const s = o.data.score_summary?.[seat];
      b.append(
        element("span", "", seatLabel(latest!, seat)),
        element("strong", "", s ? String(s.total) : "-"),
      );
      if (s) {
        b.title =
          AREAS.map((a) => `${a}: ${s.areas[a]}`).join(" · ") +
          ` · Emblem points: ${s.emblem_points}`;
      }
      scores.append(b);
    }
    renderResources();
    if (latest) renderMind(latest);
  }
  /** The recorded decision of the shown replay position: who, the move, and the probabilities or an explanation. */
  function renderMind(view: View) {
    type Note = { player: number; action: string; explanation?: string; meta?: { probabilities?: { action: string; probability: number }[] } };
    const timeline = (view as { replay_timeline?: { positions: { decision?: Note | null }[] } }).replay_timeline;
    const at = (view as { replay_position?: number }).replay_position;
    mindPanel.hidden = view.status !== "replay" || !timeline;
    if (mindPanel.hidden || !timeline) return;
    const note = at != null ? timeline.positions[at]?.decision : null;
    if (!note) {
      mindBox.replaceChildren(element("div", "bc-empty", at === 0 ? "Start of the game." : "No decision at this position (an automatic step)."));
      return;
    }
    const who = element("div", "who");
    who.append(element("b", "", seatLabel(view, note.player)));
    const chosen = element("div", "chosen", `➜ ${note.action}`);
    const parts: HTMLElement[] = [who, chosen];
    const probs = note.meta?.probabilities;
    const explanation = note.explanation && !/^Selected by the greedy observation policy\.?$/.test(note.explanation) ? note.explanation : "";
    if (probs?.length) {
      const list = element("div", "probs");
      for (const p of probs) {
        const row = element("div", `prob${p.action === note.action ? " on" : ""}`);
        const bar = element("i");
        bar.style.width = `${(p.probability * 100).toFixed(1)}%`;
        row.append(element("span", "", p.action), bar, element("b", "", `${(p.probability * 100).toFixed(0)}%`));
        list.append(row);
      }
      parts.push(list);
    } else parts.push(element("div", explanation ? "expl" : "bc-empty", explanation ? `“${explanation}”` : "No recorded reasoning."));
    mindBox.replaceChildren(...parts);
  }
  /** Rerolls and +1 of the seat this screen plays (the solo seat for a watcher): earned, used and left. */
  function renderResources() {
    const o = latest?.observation;
    const seat = o ? (o.viewer ?? (o.data.sheets.length === 1 ? 0 : null)) : null;
    resources.hidden = seat === null;
    if (!o || seat === null) return;
    const s = o.data.sheets[seat];
    const who = seat === o.viewer ? "Your resources" : `${seatLabel(latest!, seat)}: resources`;
    const line = (icon: string, name: string, left: number, earned: number, used: number) => {
      const row = element("div", "bc-resource-row");
      row.append(element("span", "bc-resource-name", `${icon} ${name}`), element("strong", "", `${left} left`), element("small", "", `${earned} earned · ${used} used`));
      return row;
    };
    const emblems = element("div", "bc-resource-row");
    emblems.append(element("span", "bc-resource-name", "Emblems"), emblemIcon(), element("strong", "", String(s.emblem_count)));
    resources.replaceChildren(element("h4", "", who),
      line("↻", "Rerolls", s.rerolls, s.rerolls_earned ?? s.rerolls, s.rerolls_used ?? 0),
      line("+1", "Extra dice", s.extras, s.extras_earned ?? s.extras, s.extras_used ?? 0), emblems);
  }
  /** Recent moves in words; a published window adds what each sheet entered in it (never earlier than the reveal). */
  function renderMoves(view: View, events: GameEvent[], before: any[] | undefined) {
    const names = seatNames(view).map((n, i) => (i === view.observation?.viewer ? "You" : n));
    const lines = events.flatMap((e) => {
      if (e.kind === "window_published") {
        const entries = (e.payload.entries as WindowEntry[] | undefined) ?? windowEntries(before, e.payload.sheets);
        const solo = view.config.players === 1;
        const what = e.payload.phase === "round_bonus" ? "round bonus" : solo ? "simulated turn" : "shared tray";
        return entries.map((x) => x.area === "extras_used"
          ? `${names[x.player]} used ${x.target} +1 (${what})`
          : `${names[x.player]} entered ${fieldText(x)}${x.value != null ? ` (${x.value})` : ""} (${what})`);
      }
      const text = describeDiceEvent(e, names);
      return text ? [text] : [];
    });
    if (view.status === "replay") moves = lines.reverse();
    else if (lines.length) moves = [...lines.reverse(), ...moves].slice(0, 30);
    movesList.replaceChildren(...(moves.length ? moves : ["Moves appear here as they happen."]).map((m) => element("li", moves.length ? "" : "bc-empty", m)));
  }
  window.addEventListener(
    "keydown",
    (e) => {
      if (
        disposed ||
        host.querySelector(".modal") ||
        !(
          document.activeElement === document.body ||
          host.contains(document.activeElement)
        ) ||
        /INPUT|TEXTAREA|SELECT/.test((e.target as HTMLElement).tagName)
      )
        return;
      if (/^[1-6]$/.test(e.key)) {
        selectDie(DICE[Number(e.key) - 1]);
        e.preventDefault();
      } else if (e.key === "Escape") {
        selection.selectedDie = null;
        areaFilter = null;
        refreshSelection();
      }
    },
    { signal: controls.signal },
  );
  return {
    update(
      view: View,
      events: GameEvent[] = [],
      actions: ActionList = { decision_id: null, actions: [] },
    ) {
      if (disposed) return;
      const o = view.observation;
      // A lobby has no observation revision, so the seat roster is what tells one lobby update from the next.
      const roster = o ? "" : JSON.stringify([view.config.players, view.seats]);
      if (
        !pending &&
        latest?.status === view.status &&
        latest?.observation?.revision === o?.revision &&
        latestRoster === roster &&
        JSON.stringify(latestActions) === JSON.stringify(actions) &&
        events.length === 0
      )
        return;
      if (
        view.status !== "replay" &&
        !options.readOnly &&
        latest?.observation &&
        o &&
        o.revision < latest.observation.revision
      )
        return;
      // The sheets just before this update: a published window is compared with them (only for a contiguous step).
      const step = (v: View | null) => (v as { replay_position?: number } | null)?.replay_position;
      const contiguous = view.status !== "replay" || (step(latest) != null && step(latest)! + 1 === step(view));
      const previousSheets = contiguous ? latest?.observation?.data.sheets : undefined;
      latest = view;
      latestRoster = roster;
      if (!options.gameBar) bar.setTitle(`Table ${view.table_id.slice(0, 8)}${o?.viewer == null ? " · watching" : ""}`);
      latestActions = actions;
      pending = false;
      const legal = options.readOnly ? [] : actionContext(view, actions);
      selection.update(o?.decision?.id ?? null, legal);
      if (!selection.selectedDie) areaFilter = null;
      scene?.update(view, events, legal);
      if (o) {
        status.textContent = statusText(view);
      }
      if (!o) {
        const seats = view.seats ?? [];
        const ready = seats.filter((s) => s.occupied && s.availability?.status !== "unavailable").length;
        status.textContent = `Waiting for players · ${ready} of ${Math.max(view.config.players, seats.length)} seats ready`;
      }
      cameraButtons.get("opponents")!.disabled =
        (o?.data.sheets.length ?? view.config.players) < 2;
      renderScores();
      if (o) renderMoves(view, events, previousSheets);
      if (o && !openedView) {
        // An address such as #table=<id>&view=sheet opens the table in that camera.
        openedView = true;
        const wanted = new URLSearchParams(location.hash.slice(1)).get("view");
        if (wanted === "sheet" || wanted === "opponents") cameraButtons.get(wanted)?.click();
      }
      renderInteraction();
      renderSurrender(view, actions);
      insets();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      controls.abort();
      resize.disconnect();
      if (document.fullscreenElement === host)
        void document.exitFullscreen().catch(() => {});
      scene?.dispose();
      table?.dispose();
      host.remove();
    },
  };
}

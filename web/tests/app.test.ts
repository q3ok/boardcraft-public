async function pickGame(root: HTMLElement) {
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Game"]')).toBeTruthy());
  const game = root.querySelector<HTMLSelectElement>('[aria-label="Game"]')!;
  game.value = "dice_circuit"; game.dispatchEvent(new Event("change"));
  const count = root.querySelector<HTMLSelectElement>('[aria-label="Number of players"]')!;
  count.value = "1"; count.dispatchEvent(new Event("change"));
}
import { it, expect, vi, afterEach, beforeEach } from "vitest";
const captured = vi.hoisted(() => ({
  updates: [] as any[],
  options: undefined as any,
  onExit: undefined as (() => void) | undefined,
}));
vi.mock("../src/game-view", () => ({
  mountGameView: (root: HTMLElement, options: any) => {
    captured.options=options;
    captured.onExit = options.onExit;
    const host = document.createElement("div");
    host.className = "bc-bottom";
    root.append(host);
    return {
      update: (...args: any[]) => captured.updates.push(args),
      dispose: () => host.remove(),
    };
  },
}));
import { mountPlayApp } from "../src/app";
import sheet from "../../src/boardcraft/games/dice_circuit/data/base-sheet.json";
beforeEach(() => {
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => data.set(k, v),
    clear: () => data.clear(),
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  history.replaceState(null, "", "/");
  sessionStorage.clear();
  document.body.replaceChildren();
  captured.updates = [];
  captured.onExit = undefined;
});
const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
const response = (value: unknown) =>
  new Response(typeof value === "string" ? value : JSON.stringify(value), {
    headers: { "Content-Type": typeof value === "string" ? "text/plain" : "application/json" },
  });
function completedFixture(replayRoute = false) {
  const table = "a".repeat(32);
  // A finished table's replay opens from its own address (the Replays page); the game bar has no replay button.
  history.replaceState(null, "", replayRoute ? `/#/replay/${table}` : `/#table=${table}`);
  const observation = {
    game_id: "dice_circuit",
    viewer: null,
    revision: 100,
    decision: null,
    data: { round: 6, event_cursor: 0, sheets: [] },
  };
  const view = {
    table_id: table,
    status: "completed",
    config: { game_id: "dice_circuit", variant: "base", players: 1 },
    observation,
  };
  return {
    view,
    observation,
    frame: {
      observation: {
        ...observation,
        revision: 0,
        data: { ...observation.data, round: 1 },
      },
      events: [],
      position: 0,
      total_positions: 5,
    },
  };
}
it.each(["view", "actions", "events"])(
  "a live %s poll never replaces the selected replay frame",
  async (endpoint) => {
    const { view, frame } = completedFixture(true);
    let hold = false;
    let release: ((r: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (
          hold &&
          url.includes(`/${endpoint}${endpoint === "events" ? "?" : ""}`)
        )
          return new Promise<Response>((resolve) => {
            release = resolve;
          });
        if (url.includes("/replay?")) return Promise.resolve(response(frame));
        return Promise.resolve(
          response(
            url.endsWith("/view")
              ? hold && endpoint === "events"
                ? {
                    ...view,
                    observation: {
                      ...view.observation,
                      data: { ...view.observation.data, event_cursor: 1 },
                    },
                  }
                : view
              : url.endsWith("/actions")
                ? { decision_id: null, actions: [] }
                : url.endsWith("/board")
                  ? sheet
                  : "Rules",
          ),
        );
      }),
    );
    const root = document.createElement("main");
    document.body.append(root);
    const app = mountPlayApp(root);
    try {
      await vi.waitFor(() =>
        expect(root.textContent).toContain("decision 1 / 5"),
      );
      expect(root.textContent).not.toContain("▶ Replay");
      hold = true;
      document.dispatchEvent(new Event("visibilitychange"));
      await tick();
      // While the replay is open the live table is not polled, so no late answer can replace the frame.
      expect(release).toBeUndefined();
      await tick();
      expect(captured.updates.at(-1)[0].status).toBe("replay");
      expect(captured.updates.at(-1)[0].observation.data.round).toBe(1);
    } finally {
      app.dispose();
    }
  },
);
it("passes the selected perspective's cursor and revision to the replay widget", async () => {
  const { view, frame } = completedFixture(true);
  const completed = { ...view, revision: 100, event_cursor: 37,
    control: { autopilot: false, commands: [], can_reclaim: false, deadline: null } };
  frame.observation.data.event_cursor = 2;
  vi.stubGlobal("fetch", async (url: string) => response(
    url.endsWith("/view") ? completed : url.includes("/replay?") ? frame :
    url.endsWith("/actions") ? { decision_id: null, actions: [] } : url.endsWith("/board") ? sheet : "Rules",
  ));
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  try {
    await vi.waitFor(() => expect(root.textContent).toContain("decision 1 / 5"));
    const dto = captured.updates.at(-1)[0];
    expect(dto).toMatchObject({ status: "replay", revision: 0, event_cursor: 2, observation: frame.observation });
    expect(dto.control).toBeUndefined();
  } finally {
    app.dispose();
  }
});
it("does not write into a disposed owner when initial discovery aborts", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) =>
          options.signal!.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          ),
        ),
    ),
  );
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  app.dispose();
  await tick();
  expect(root.childElementCount).toBe(0);
});
it("ignores an obsolete live failure after returning to the hub", async () => {
  const { view } = completedFixture();
  let hold = false;
  let rejectPoll: ((e: Error) => void) | undefined;
  const games = [
    {
      game_id: "dice_circuit",
      name: "Dice Circuit",
      variants: ["base"],
      min_players: 1,
      max_players: 4,
      bot_kinds: ["random", "greedy"],
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (hold && url.endsWith("/view"))
        return new Promise<Response>((_resolve, reject) => {
          rejectPoll = reject;
        });
      return Promise.resolve(
        response(
          url === "/api/games"
            ? games
            : url.endsWith("/view")
              ? view
              : url.endsWith("/actions")
                ? { decision_id: null, actions: [] }
                : url.endsWith("/board")
                  ? sheet
                  : "Rules",
        ),
      );
    }),
  );
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  try {
    await vi.waitFor(() => expect(captured.updates.at(-1)?.[0].status).toBe("completed"));
    expect(root.textContent).not.toContain("▶ Replay");
    hold = true;
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.waitFor(() => expect(rejectPoll).toBeTypeOf("function"));
    captured.onExit!();
    await vi.waitFor(() =>
      expect(root.querySelector(".bc-hub")).not.toBeNull(),
    );
    rejectPoll!(new Error("Old live request failed"));
    await tick();
    expect(root.querySelector(".bc-app-error")).toBeNull();
  } finally {
    app.dispose();
  }
});
it("ignores an older replay failure after returning to the live result", async () => {
  const { view, frame } = completedFixture(true);
  let holdResult = false;
  let rejectFrame: ((e: Error) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (holdResult && url.endsWith("/view"))
        return new Promise<Response>(() => {});
      if (url.includes("/replay?position=2"))
        return new Promise<Response>((_resolve, reject) => {
          rejectFrame = reject;
        });
      return Promise.resolve(
        response(
          url.includes("/replay?")
            ? frame
            : url.endsWith("/view")
              ? view
              : url.endsWith("/actions")
                ? { decision_id: null, actions: [] }
                : url.endsWith("/board")
                  ? sheet
                  : "Rules",
        ),
      );
    }),
  );
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  try {
    await vi.waitFor(() => expect(root.querySelector("input")).not.toBeNull());
    const slider = root.querySelector("input")!;
    slider.value = "2";
    slider.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(rejectFrame).toBeTypeOf("function"));
    holdResult = true;
    [...root.querySelectorAll("button")]
      .find((b) => b.textContent === "✕ Close replay")!
      .click();
    rejectFrame!(new Error("Old replay failed"));
    await tick();
    expect(root.querySelector(".bc-app-error")).toBeNull();
    expect(root.querySelector(".tc-replay")).toBeNull();
  } finally {
    app.dispose();
  }
});
it("does not replace a new owner screen when an old create request succeeds after disposal", async () => {
  const games = [
    {
      game_id: "dice_circuit",
      name: "Dice Circuit",
      variants: ["base"],
      min_players: 1,
      max_players: 4,
      bot_kinds: ["random", "greedy"],
    },
  ];
  let release: ((r: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      url === "/api/tables"
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : Promise.resolve(
            response(
              url === "/api/games"
                ? games
                : url.endsWith("/bots")
                  ? builtinDescriptors
                  : { credential: "old-seat", seat: 0 },
            ),
          ),
    ),
  );
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  await pickGame(root);
  await vi.waitFor(() =>
    expect(root.querySelector(".bc-create")).not.toBeNull(),
  );
  await vi.waitFor(() => expect(root.querySelector<HTMLButtonElement>('.bc-create button[type="submit"]')!.disabled).toBe(false));
  root
    .querySelector(".bc-create")!
    .dispatchEvent(new Event("submit", { cancelable: true }));
  await vi.waitFor(() => expect(release).toBeTypeOf("function"));
  app.dispose();
  root.textContent = "New owner screen";
  release!(
    response({ table_id: "b".repeat(32), creator_credential: "old-creator" }),
  );
  await tick();
  expect(root.textContent).toBe("New owner screen");
  expect(sessionStorage.getItem("boardcraft.session")).toBeNull();
});

const builtinDescriptors = [
  {
    choice_id: "random",
    display_name: "Random bot",
    kind: "builtin",
    reference: {},
  },
  {
    choice_id: "greedy",
    display_name: "Greedy bot",
    kind: "builtin",
    reference: {},
  },
];
const catalogGames = [
  {
    game_id: "dice_circuit",
    name: "Dice Circuit",
    variants: ["base"],
    min_players: 1,
    max_players: 4,
    bot_kinds: ["random", "greedy"],
  },
];
it("loads real bot labels, preserves selected IDs across seat count redraw, and never preselects a model", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        response(
          url === "/api/games"
            ? catalogGames
            : [
                ...builtinDescriptors,
                {
                  choice_id: "fixture-policy",
                  display_name: "Synthetic MLP",
                  kind: "model",
                  reference: {},
                },
              ],
        ),
      ),
    ),
  );
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  await pickGame(root);
  try {
    await vi.waitFor(() => expect(root.textContent).toContain("Synthetic MLP"));
    const seat = root.querySelector<HTMLSelectElement>(
      '[aria-label="Player 1 type"]',
    )!;
    expect(seat.value).toBe("human");
    seat.value = "fixture-policy";
    seat.dispatchEvent(new Event("change"));
    const count = root.querySelector<HTMLSelectElement>(
      '[aria-label="Number of players"]',
    )!;
    count.value = "3";
    count.dispatchEvent(new Event("change"));
    expect(
      root.querySelector<HTMLSelectElement>('[aria-label="Player 1 type"]')!
        .value,
    ).toBe("fixture-policy");
    expect(
      root.querySelector<HTMLSelectElement>('[aria-label="Player 2 type"]')!
        .value,
    ).toBe("greedy");
    count.value = "4";
    count.dispatchEvent(new Event("change"));
    const fourth = root.querySelector<HTMLSelectElement>(
      '[aria-label="Player 4 type"]',
    )!;
    fourth.value = "fixture-policy";
    fourth.dispatchEvent(new Event("change"));
    count.value = "2";
    count.dispatchEvent(new Event("change"));
    const first = root.querySelector<HTMLSelectElement>(
      '[aria-label="Player 1 type"]',
    )!;
    first.value = "random";
    first.dispatchEvent(new Event("change"));
    count.value = "4";
    count.dispatchEvent(new Event("change"));
    expect(
      root.querySelector<HTMLSelectElement>('[aria-label="Player 4 type"]')!
        .value,
    ).toBe("fixture-policy");
  } finally {
    app.dispose();
  }
});
it.each(["pending", "failed"])(
  "blocks creation while bot discovery is %s",
  async (mode) => {
    let createCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url === "/api/games")
          return Promise.resolve(response(catalogGames));
        if (url === "/api/tables") {
          createCalls++;
          return Promise.resolve(response({}));
        }
        return mode === "pending"
          ? new Promise(() => {})
          : Promise.resolve(new Response("", { status: 503 }));
      }),
    );
    const root = document.createElement("main");
    document.body.append(root);
    const app = mountPlayApp(root);
    await pickGame(root);
    try {
      await vi.waitFor(() =>
        expect(root.querySelector(".bc-create")).not.toBeNull(),
      );
      await tick();
      expect(
        root.querySelector<HTMLButtonElement>(
          '.bc-create button[type="submit"]',
        )!.disabled,
      ).toBe(true);
      root
        .querySelector(".bc-create")!
        .dispatchEvent(new Event("submit", { cancelable: true }));
      await tick();
      expect(createCalls).toBe(0);
    } finally {
      app.dispose();
    }
  },
);
it("ignores old game catalog replies and keeps empty model discovery honest", async () => {
  let release: ((r: Response) => void) | undefined;
  const games = [
    ...catalogGames,
    {
      ...catalogGames[0],
      game_id: "other",
      name: "Other game",
      bot_kinds: ["random"],
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      url === "/api/games"
        ? Promise.resolve(response(games))
        : url.includes("/dice_circuit/")
          ? new Promise<Response>((resolve) => {
              release = resolve;
            })
          : Promise.resolve(response(builtinDescriptors.slice(0, 1))),
    ),
  );
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  await pickGame(root);
  try {
    await vi.waitFor(() => expect(release).toBeTypeOf("function"));
    const select = root.querySelector<HTMLSelectElement>(
      '[aria-label="Game"]',
    )!;
    select.value = "other";
    select.dispatchEvent(new Event("change"));
    await vi.waitFor(() =>
      expect(root.querySelector(".bc-create")!.textContent).toContain(
        "Random bot",
      ),
    );
    release!(
      response([
        {
          choice_id: "obsolete",
          display_name: "Obsolete policy",
          kind: "model",
          reference: {},
        },
      ]),
    );
    await tick();
    expect(root.textContent).not.toContain("Obsolete policy");
    expect(
      [...root.querySelectorAll('[aria-label="Player 1 type"] option')].map(
        (e) => e.textContent,
      ),
    ).toEqual(["Human", "Agent", "Random bot", "Empty"]);
  } finally {
    app.dispose();
  }
});
it("uses the pinned display name in a waiting seat", async () => {
  const table = "c".repeat(32);
  history.replaceState(null, "", `/#table=${table}`);
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        response({
          table_id: table,
          status: "lobby",
          config: { game_id: "dice_circuit", variant: "base", players: 1 },
          observation: null,
          seats: [
            {
              seat: 0,
              kind: "fixture",
              display_name: "Frozen policy name",
              occupied: true,
            },
          ],
        }),
      ),
    ),
  );
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  try {
    // The waiting window lists only free seats (as in the shared design); the raw kind never shows.
    await vi.waitFor(() => expect(root.textContent).toContain("Free seats: -"));
    expect(root.textContent).not.toContain("fixture");
  } finally {
    app.dispose();
  }
});
it("ignores a late older replay request while preserving its visible frame position", async () => {
  const table = "a".repeat(32);
  sessionStorage.setItem(
    "boardcraft.session",
    JSON.stringify({
      table,
      credential: "seat-capability",
      gameId: "dice_circuit",
    }),
  );
  history.replaceState(null, "", `/#/replay/${table}`);
  const root = document.createElement("main");
  document.body.append(root);
  const waiting = new Map<number, (v: Response) => void>();
  const observation = {
    game_id: "dice_circuit",
    viewer: 0,
    revision: 100,
    decision: null,
    data: { round: 6, event_cursor: 0, sheets: [] },
  };
  const view = {
    table_id: table,
    status: "completed",
    config: { game_id: "dice_circuit", variant: "base", players: 1 },
    observation,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/replay?")) {
        const position = Number(url.split("=").at(-1));
        return new Promise<Response>((resolve) =>
          waiting.set(position, resolve),
        );
      }
      const body = url.endsWith("/view")
        ? view
        : url.endsWith("/actions")
          ? { decision_id: null, actions: [] }
          : url.endsWith("/board")
            ? sheet
            : "Rules";
      return Promise.resolve(
        new Response(typeof body === "string" ? body : JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": typeof body === "string" ? "text/plain" : "application/json" },
        }),
      );
    }),
  );
  const app = mountPlayApp(root);
  await vi.waitFor(() => expect(waiting.has(0)).toBe(true));
  const frame = (position: number) =>
    new Response(
      JSON.stringify({
        observation: {
          ...observation,
          revision: position,
          data: { ...observation.data, round: position + 1 },
        },
        events: [],
        position,
        total_positions: 5,
      }),
      { headers: { "Content-Type": "application/json" } },
    );
  waiting.get(0)!(frame(0));
  await vi.waitFor(() => expect(root.querySelector("input")).not.toBeNull());
  const slider = root.querySelector("input")!;
  slider.value = "2";
  slider.dispatchEvent(new Event("change"));
  slider.value = "3";
  slider.dispatchEvent(new Event("change"));
  await vi.waitFor(() => expect(waiting.has(3)).toBe(true));
  waiting.get(3)!(frame(3));
  await vi.waitFor(() => expect(root.textContent).toContain("decision 4 / 5"));
  waiting.get(2)!(frame(2));
  await new Promise((resolve) => setTimeout(resolve, 10));
  expect(root.textContent).toContain("decision 4 / 5");
  expect(captured.updates.at(-1)[0].observation.data.round).toBe(4);
  app.dispose();
});

it.each([true,false])("uses returned game metadata and separate table/engine/cursor numbers on a deep link (saved %s)",async saved=>{
  const table="c".repeat(32),view={table_id:table,status:"playing",event_cursor:31,revision:6,config:{game_id:"rentcraft",players:2,variant:"base"},observation:{viewer:saved?0:null,revision:6,decision:saved?{id:"d6",player:0,kind:"pre_roll"}:null,data:{event_cursor:78}}};
  history.replaceState(null,"","/#table="+table);
  if(saved)sessionStorage.setItem("boardcraft.session",JSON.stringify({table,credential:"seat-token",gameId:"obsolete"}));
  const requests:{path:string;body?:any;auth:string|null}[]=[];
  vi.stubGlobal("fetch",async(path:string,init:RequestInit)=>{
    requests.push({path,body:init.body?JSON.parse(String(init.body)):undefined,auth:new Headers(init.headers).get("Authorization")});
    if(path.endsWith("/commands"))return new Response(JSON.stringify({detail:"Sell the buildings first."}),{status:400,headers:{"Content-Type":"application/json"}});
    return response(path.endsWith("/view")?view:path.endsWith("/actions")?{expected_revision:46,decision_id:saved?"d6":null,actions:[],commands:[]}:path.endsWith("/rules")?"Rent rules":{spaces:[]});
  });
  const root=document.createElement("div"),app=mountPlayApp(root);
  try{
    await vi.waitFor(()=>expect(captured.updates.length).toBe(1));
    expect(captured.options.gameId).toBe("rentcraft");expect(captured.options.readOnly).toBe(!saved);
    expect(requests.some(r=>r.path==="/api/games/rentcraft/board")).toBe(true);
    expect(requests.some(r=>r.path==="/api/games/rentcraft/settings")).toBe(true);
    expect(JSON.parse(sessionStorage.getItem("boardcraft.session")!).gameId).toBe("rentcraft");
    expect(requests.some(r=>r.path.includes("/events?"))).toBe(false);
    if(!saved) expect(captured.updates[0][2]).not.toHaveProperty("expected_revision");
    if(saved){
      await expect(captured.options.onCommand({expectedRevision:46,decisionId:"d6",kind:"propose_trade",params:{target:1}})).rejects.toThrow("Sell the buildings first.");
      expect(requests.find(r=>r.path.endsWith("/commands"))).toMatchObject({auth:"Bearer seat-token",body:{expected_revision:46,decision_id:"d6",kind:"propose_trade",params:{target:1}}});
      expect(requests.filter(r=>r.path.endsWith("/view"))).toHaveLength(2);
      app.dispose();const sent=requests.length;
      await expect(captured.options.onCommand({expectedRevision:46,kind:"reclaim",params:{}})).rejects.toThrow();
      expect(requests).toHaveLength(sent);
    }
  }finally{app.dispose();}
});

it("names the standalone hub for the platform before a game has been chosen",async()=>{
  const page="../index.html?raw";
  const {default:html}=await import(page);
  expect(html).toContain("<title>Boardcraft</title>");
});

it("binds an off-turn command menu to the observation taken at its protected table revision", async () => {
  const table = "d".repeat(32), sent: unknown[] = [];
  history.replaceState(null, "", "/#table=" + table);
  sessionStorage.setItem("boardcraft.session", JSON.stringify({ table, credential: "waiting-seat" }));
  let revision = 4;
  vi.stubGlobal("fetch", async (path: string, init: RequestInit) => {
    if (path.endsWith("/actions")) return response({ decision_id: null, actions: [], expected_revision: revision, commands: [{ kind: "resign", scope: "seat", label: "Resign" }] });
    if (path.endsWith("/view")) {
      const moved = revision === 5;
      revision = 5;
      return response({ table_id: table, status: "playing", config: { game_id: "rentcraft", players: 3 }, event_cursor: 0,
        observation: { viewer: 0, revision: moved ? 1 : 0, decision: null, data: { event_cursor: 0, position: moved ? 8 : 0 } } });
    }
    if (path.endsWith("/commands")) sent.push(JSON.parse(String(init.body)));
    return response({});
  });
  const app = mountPlayApp(document.createElement("main"));
  try {
    await tick();
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.waitFor(() => expect(captured.updates.length).toBeGreaterThan(0));
    for (const [view, , menu] of captured.updates)
      if (menu.expected_revision === 5) expect(view.observation.data.position).toBe(8);
    const [latest, , menu] = captured.updates.at(-1);
    expect(latest.observation.revision).toBe(1);
    expect(menu.commands[0].kind).toBe("resign");
    await captured.options.onCommand({ kind: "resign", params: {}, expectedRevision: menu.expected_revision });
    expect(sent).toEqual([{ kind: "resign", params: {}, expected_revision: 5 }]);
  } finally { app.dispose(); }
});

it("names the table by its server number in the game bar", async () => {
  const { view } = completedFixture();
  vi.stubGlobal("fetch", async (url: string) => response(
    url.endsWith("/summary") ? { table_id: view.table_id, number: 17 } : url.endsWith("/view") ? view :
    url.endsWith("/actions") ? { decision_id: null, actions: [] } : url.endsWith("/board") ? sheet : "Rules",
  ));
  const root = document.createElement("main");
  document.body.append(root);
  const app = mountPlayApp(root);
  try {
    await vi.waitFor(() => expect(root.textContent).toContain("Table #17 · watching"));
  } finally {
    app.dispose();
  }
});

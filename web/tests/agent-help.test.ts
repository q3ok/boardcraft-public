import { afterEach, expect, it, vi } from "vitest";
import { agentPrompt, mcpCommand, mcpConfig, mountAgentHelp } from "../src/agent-help";

const origin = "http://127.0.0.1:8123";
const table = "0123456789abcdef0123456789abcdef";
const context = { origin, gameId: "dice_circuit", gameName: "Dice Circuit", table };
const seat = (n: number, kind: string, occupied: boolean) => ({ seat: n, kind, occupied, display_name: kind === "agent" ? "Agent" : "Human" });

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

function mount(seats = [seat(0, "agent", false), seat(1, "human", false), seat(2, "agent", true), seat(3, "agent", false)]) {
  const parent = document.createElement("div");
  document.body.append(parent);
  const help = mountAgentHelp(parent, context);
  help.update(seats);
  return { parent, help };
}
const prompts = (parent: HTMLElement) => [...parent.querySelectorAll<HTMLTextAreaElement>("textarea[data-agent-prompt]")];
const press = (parent: HTMLElement, label: string) => [...parent.querySelectorAll("button")].find(b => b.textContent === label || b.getAttribute("aria-label") === label)!.click();

it("agentHelp prompt names the exact origin, game, table and zero-based API seat without any secret", () => {
  const text = agentPrompt({ ...context, seat: 2 });
  expect(text).toContain("Boardcraft server: " + origin);
  expect(text).toContain("Game: Dice Circuit (dice_circuit)");
  expect(text).toContain("Table ID: " + table);
  expect(text).toContain("Your seat: Player 3 (API seat 2");
  for (const word of ["get_rules", "join_game", "observe_game", "legal_actions", "apply_action", "get_result"])
    expect(text).toContain(word);
  expect(text).toMatch(/do not create a new table/i);
  expect(text).toMatch(/wait for the table creator to start/i);
  expect(text).toMatch(/until the (table|game) (status )?is completed/i);
  expect(text).toMatch(/Rentcraft.*submit_command|submit_command.*advertis/is);
  expect(text).not.toMatch(/Bearer|creator_credential|secret|training|[A-Z]:[\\/]|\/Users\//);
});

it("agentHelp setup text uses the actual origin and contains no machine-specific path", () => {
  expect(mcpCommand(origin + "/")).toBe("boardcraft mcp --server-url " + origin);
  expect(JSON.parse(mcpConfig(origin))).toEqual({ command: "boardcraft", args: ["mcp", "--server-url", origin] });
  expect(mcpConfig(origin)).not.toMatch(/[A-Z]:[\\/]|\/home\/|\/Users\//);
});

it("agentHelp shows one copyable prompt per open agent seat and none for joined or human seats", () => {
  const { parent } = mount();
  const areas = prompts(parent);
  expect(areas.map(a => a.dataset.agentPrompt)).toEqual(["0", "3"]);
  expect(areas[0].readOnly).toBe(true);
  expect(areas[0].value).toBe(agentPrompt({ ...context, seat: 0 }));
  expect(areas[1].value).toContain("API seat 3");
  expect(areas[1].getAttribute("aria-label")).toBe("Prompt for Player 4");
  expect(parent.textContent).not.toContain("API seat 2");
});

it("agentHelp copies the exact prompt and reports success", async () => {
  const writeText = vi.fn(async () => {});
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const { parent } = mount();
  press(parent, "Copy prompt for Player 4");
  await vi.waitFor(() => expect(parent.querySelector("[role=status]")!.textContent).toMatch(/Prompt for Player 4 copied/));
  expect(writeText).toHaveBeenCalledExactlyOnceWith(agentPrompt({ ...context, seat: 3 }));
});

it.each(["rejected", "missing"])("agentHelp clipboard %s leaves selected text and an accurate status", async mode => {
  vi.stubGlobal("navigator", mode === "missing" ? {} : { clipboard: { writeText: async () => { throw new Error("denied"); } } });
  const { parent } = mount();
  press(parent, "Copy prompt for Player 1");
  const area = prompts(parent)[0];
  await vi.waitFor(() => expect(parent.textContent).toMatch(/could not copy automatically/i));
  expect(parent.textContent).not.toMatch(/Prompt for Player 1 copied/);
  expect(area.selectionStart).toBe(0);
  expect(area.selectionEnd).toBe(area.value.length);
});

it("agentHelp says the prompt only works with a configured Boardcraft MCP and offers setup", async () => {
  const writeText = vi.fn(async () => {});
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const { parent } = mount();
  expect(parent.textContent).toMatch(/already has the Boardcraft MCP server configured/);
  expect(parent.textContent).toMatch(/does not connect an agent by itself/i);
  const details = parent.querySelector<HTMLDetailsElement>("details[data-mcp-setup]")!;
  expect(details.open).toBe(false);
  expect([...details.querySelectorAll("textarea")].map(a => a.value)).toContain("boardcraft mcp --server-url " + origin);
  press(parent, "Copy MCP command");
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("boardcraft mcp --server-url " + origin));
  press(parent, "Copy MCP configuration");
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith(mcpConfig(origin)));
});

it("agentHelp keeps prompt elements and setup state across seat refreshes and drops joined seats", () => {
  const { parent, help } = mount();
  const first = prompts(parent)[0];
  parent.querySelector<HTMLDetailsElement>("details[data-mcp-setup]")!.open = true;
  help.update([seat(0, "agent", false), seat(1, "human", true), seat(2, "agent", true), seat(3, "agent", true)]);
  expect(prompts(parent)).toEqual([first]);
  expect(parent.querySelector<HTMLDetailsElement>("details[data-mcp-setup]")!.open).toBe(true);
  help.update([seat(0, "agent", true)]);
  expect(prompts(parent)).toHaveLength(0);
  expect(parent.querySelector("details[data-mcp-setup]")).toBeNull();
  expect(help.root.hidden).toBe(true);
});

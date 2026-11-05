import { button, element } from "./dom";

/** What an agent needs to find one seat of one table on one Boardcraft server. No credentials are ever part of it. */
export interface AgentPromptInput {
  origin: string;
  gameId: string;
  gameName: string;
  table: string;
  seat: number;
}
export interface AgentHelpContext extends Omit<AgentPromptInput, "seat"> {}
interface HelpSeat { seat: number; kind: string; occupied: boolean }

const cleanOrigin = (origin: string) => origin.replace(/\/+$/, "");

export function mcpCommand(origin: string) {
  return `boardcraft mcp --server-url ${cleanOrigin(origin)}`;
}
export function mcpConfig(origin: string) {
  return JSON.stringify({ command: "boardcraft", args: ["mcp", "--server-url", cleanOrigin(origin)] }, null, 2);
}
export function agentPrompt(input: AgentPromptInput) {
  const { gameId, gameName, table, seat } = input;
  return [
    `You are joining a Boardcraft game as an agent player.`,
    ``,
    `Boardcraft server: ${cleanOrigin(input.origin)}`,
    `Game: ${gameName} (${gameId})`,
    `Table ID: ${table}`,
    `Your seat: Player ${seat + 1} (API seat ${seat}; the API counts seats from 0)`,
    ``,
    `Use the Boardcraft gameplay MCP tools connected to this same server. Do not create a new table, and do not guess another server or table. If the tools are missing, or this table cannot be found, stop and report that instead.`,
    ``,
    `1. Call get_rules for ${gameId} and read the rules.`,
    `2. Call join_game with this table ID and seat ${seat}. Keep the seat credential it returns private and use it only for this seat.`,
    `3. Call observe_game and legal_actions with your seat credential. In Rentcraft, legal_actions also advertises commands that you send with submit_command.`,
    `4. Wait for the table creator to start the game. You cannot start it. Keep reading observe_game until the status is playing.`,
    `5. On each of your decisions choose one of the legal actions and send it with apply_action. Read the state again after every change, and continue until the table status is completed.`,
    `6. Then call get_result and report the outcome.`,
  ].join("\n");
}

/** Copies text; on failure the caller's selectable text is selected and the reason is stated accurately. */
async function copy(text: string, area: HTMLTextAreaElement | undefined, status: HTMLElement, what: string) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(text);
    status.textContent = `${what} copied.`;
  } catch {
    status.textContent = "Boardcraft could not copy automatically. The text is selected: copy it with your keyboard or browser menu.";
    if (area) {
      area.focus();
      area.select();
      area.setSelectionRange(0, area.value.length);
    }
  }
}
function copyBlock(label: string, text: string, rows: number, buttonLabel: string, status: HTMLElement, what: string, promptSeat?: number) {
  const block = element("div", "bc-copy-block");
  const area = element("textarea", "bc-copy-text");
  area.readOnly = true;
  area.rows = rows;
  area.value = text;
  area.spellcheck = false;
  area.setAttribute("aria-label", label);
  if (promptSeat !== undefined) area.dataset.agentPrompt = String(promptSeat);
  const action = button("Copy", () => void copy(text, area, status, what));
  action.setAttribute("aria-label", buttonLabel);
  action.textContent = buttonLabel.startsWith("Copy prompt") ? "Copy prompt" : buttonLabel;
  block.append(area, action);
  return block;
}

/**
 * One copyable prompt per open agent seat, plus collapsible MCP setup. "Joined" means a seat credential was issued,
 * never that an agent holds a live connection. The block keeps its elements across refreshes.
 */
export function mountAgentHelp(parent: HTMLElement, context: AgentHelpContext) {
  const root = element("section", "bc-agent-help");
  root.hidden = true;
  const intro = element("p", "bc-small");
  intro.append(
    "Each open agent seat needs its own agent. Paste one prompt into an agent that already has the Boardcraft MCP server configured. ",
    "A prompt does not connect an agent by itself; the agent joins the seat when it runs the steps below.",
  );
  const status = element("p", "bc-small");
  status.setAttribute("role", "status");
  const list = element("div", "bc-agent-prompts");
  const setup = element("details", "bc-mcp-setup");
  setup.dataset.mcpSetup = "";
  setup.append(element("summary", "", "Boardcraft MCP is not configured yet?"));
  setup.append(
    element("p", "bc-small", "Add the Boardcraft MCP server to your agent, pointing at this server. It runs on your own computer and uses this server's games and models."),
    copyBlock("MCP command", mcpCommand(context.origin), 2, "Copy MCP command", status, "MCP command"),
    copyBlock("MCP configuration", mcpConfig(context.origin), 6, "Copy MCP configuration", status, "MCP configuration"),
  );
  const blocks = new Map<number, HTMLElement>();
  root.append(element("h3", "", "Invite agents"), intro, list, status, setup);
  parent.append(root);
  return {
    root,
    update(seats: HelpSeat[]) {
      const open = seats.filter(s => s.kind === "agent" && !s.occupied);
      for (const [seat, block] of blocks)
        if (!open.some(s => s.seat === seat)) {
          block.remove();
          blocks.delete(seat);
        }
      for (const { seat } of open) {
        if (blocks.has(seat)) continue;
        const block = element("div", "bc-agent-prompt");
        block.append(
          element("strong", "", `Player ${seat + 1} · waiting for an agent`),
          copyBlock(`Prompt for Player ${seat + 1}`, agentPrompt({ ...context, seat }), 9, `Copy prompt for Player ${seat + 1}`, status, `Prompt for Player ${seat + 1}`, seat),
        );
        blocks.set(seat, block);
      }
      // Only move what is out of place, so a focused prompt or Copy button stays attached across polls.
      [...blocks].sort(([a], [b]) => a - b).forEach(([, block], index) => {
        if (list.children[index] !== block) list.insertBefore(block, list.children[index] ?? null);
      });
      root.hidden = open.length === 0;
      if (!open.length) setup.remove();
      else if (!setup.isConnected) root.append(setup);
    },
    dispose() {
      root.remove();
    },
  };
}

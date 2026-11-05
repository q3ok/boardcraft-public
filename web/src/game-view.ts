import { button, element } from "./dom";
import type { CommandInput, Settings } from "./play-types";
import type { GameBar } from "./ui/gamebar";
export interface GameViewOptions {
  gameId: string;
  boardSpec: unknown;
  rulesText: string;
  settings?:Settings;
  onAction?: (action: {
    decisionId: string;
    actionId: string;
  }) => void;
  onCommand?: (input: CommandInput) => Promise<void>;
  onExit?: () => void;
  readOnly?: boolean;
  /** Includes the table and the controls supplied by its embedding host. */
  fullscreenRoot?: HTMLElement;
  /** The shared game bar owned by the host (title, connection, host buttons); a view without one makes its own. */
  gameBar?: GameBar;
  /** Opens the move-by-move replay of a finished table (offered by the result window). */
  onReview?: () => void;
}
export interface MountedGameView {
  update(view: any, events?: any[], actions?: any): void;
  dispose(): void;
}
export type GameViewFactory = (root: HTMLElement, options: GameViewOptions) => MountedGameView;
type Loader = () => Promise<GameViewFactory>;
const modules: Record<string, Loader> = {
  dice_circuit: async () => {
    const { mountDiceCircuitView } = await import("./games/dice-circuit/view");
    return (root, options) => mountDiceCircuitView(root, { ...options, boardSpec: options.boardSpec as Parameters<typeof mountDiceCircuitView>[1]["boardSpec"] });
  },
  rentcraft: async () => (await import("./games/rentcraft/view")).mountRentcraftView,
};
export function mountLazyGameView(root: HTMLElement, options: GameViewOptions, registry: Readonly<Record<string, Loader>>): MountedGameView {
  const load = registry[options.gameId];
  if (!load)
    return mountRegisteredGameView(root, options, {});
  let disposed = false, mounted: MountedGameView | undefined;
  let queued: Parameters<MountedGameView["update"]> | undefined;
  const status = element("section", "bc-panel", "Loading the table…");
  root.append(status);
  void load().then(factory => {
    if (disposed)
      return;
    status.remove();
    mounted = factory(root, options);
    if (queued)
      mounted.update(...queued);
    queued = undefined;
  }).catch(() => {
    if (disposed)
      return;
    status.textContent = "The game view could not be loaded.";
    if (options.onExit)
      status.append(button("Back to hub", options.onExit));
    root.append(status);
  });
  return { update(...args) {
      if (disposed)
        return;
      if (mounted)
        mounted.update(...args);
      else
        queued = args;
    },
    dispose() {
      disposed = true;
      queued = undefined;
      status.remove();
      mounted?.dispose();
    } };
}
export function mountRegisteredGameView(root: HTMLElement, options: GameViewOptions, registry: Readonly<Record<string, GameViewFactory>>): MountedGameView {
  const mount = registry[options.gameId];
  if (mount)
    return mount(root, options);
  const message = element("section", "bc-waiting bc-panel");
  message.append(element("h1", "", "This game has no playable table in this edition."));
  if (options.onExit)
    message.append(button("Back to hub", options.onExit));
  root.append(message);
  return {
    update() { },
    dispose() {
      message.remove();
    },
  };
}
export function mountGameView(root: HTMLElement, options: GameViewOptions): MountedGameView {
  return mountLazyGameView(root, options, modules);
}

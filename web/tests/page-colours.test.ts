import { expect, test } from "vitest";

// Tournaments, Rankings and Replays take every colour from the shared tokens (ui/tokens.css), so each palette works there.
const COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\(/i;
const NAMED = /:\s*(?:white|black|red|green|blue|grey|gray|beige|ivory|silver|gold|yellow|orange)\b/i;
const PAGE = /\.bc-(?:page|tournaments|tlist|tmain|rankings|rank-|replays|replay-list|solo|knockout)/;

async function source(name: string): Promise<string> {
  const fsModule = "node:fs", fs = await import(/* @vite-ignore */ fsModule);
  return fs.readFileSync(`${(globalThis as any).process.cwd()}/src/${name}`, "utf8");
}
/** Every `selector { declarations }` rule of a stylesheet, media blocks unwrapped. */
function rules(css: string): { selector: string; body: string }[] {
  const out: { selector: string; body: string }[] = [];
  const flat = css.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@media[^{]*\{([\s\S]*?\})\s*\}/g, "$1");
  for (const m of flat.matchAll(/([^{}]+)\{([^{}]*)\}/g)) out.push({ selector: m[1].trim(), body: m[2] });
  return out;
}

test("the shared platform styles have no colour literals", async () => {
  const css = (await source("ui/shell.css")).replace(/\/\*[\s\S]*?\*\//g, "");
  expect(css.match(COLOUR)).toBeNull();
  expect(css.match(NAMED)).toBeNull();
});

test("no stylesheet gives the tournaments, rankings or replays pages a colour literal", async () => {
  const offenders: string[] = [];
  let scanned = 0;
  for (const file of ["style.css", "ui/shell.css", "ui/table.css"])
    for (const r of rules(await source(file))) {
      if (!PAGE.test(r.selector)) continue;
      scanned++;
      if (COLOUR.test(r.body) || NAMED.test(r.body)) offenders.push(`${file}: ${r.selector}`);
    }
  expect(scanned).toBeGreaterThan(50);
  expect(offenders).toEqual([]);
});

test("the page modules set no colours of their own", async () => {
  for (const file of ["tournaments-page.ts", "rankings.ts", "replays.ts"]) {
    const code = await source(file);
    expect(code.match(COLOUR), file).toBeNull();
    expect(code.match(/\.style\.(?:color|background\w*|border\w*Color|fill|stroke)\s*=/), file).toBeNull();
    expect(code.match(/style="[^"]*(?:color|background|fill|stroke)\s*:/), file).toBeNull();
  }
});

test("page panels at any depth, buttons and fields use the lobby's shared components", async () => {
  const shell = await source("ui/shell.css");
  expect(shell).toContain(".bc-app :is(.bc-room, .bc-page) .bc-panel");
  expect(shell).toContain(".bc-app :is(.bc-room, .bc-page) button,");
  expect(shell).toContain(".bc-app .bc-page :is(input:not([type=\"checkbox\"], [type=\"radio\"]), select, textarea)");
});

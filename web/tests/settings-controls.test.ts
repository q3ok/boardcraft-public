import { afterEach, expect, it } from "vitest";
import { settingsFields } from "../src/settings";
import type { SettingSchema } from "../src/play-types";

// Checkbox activation by click only happens for connected elements, as in a browser.
afterEach(() => { document.body.replaceChildren(); });

type Values = Record<string, unknown>;
/** `automatic` names a property that the schema marks with the generic `automatic` presentation hint. */
function mount(properties: Record<string, SettingSchema>, defaults: Values = {}, saved: Values = {}, automatic?: string) {
  const root = document.createElement("div");
  document.body.append(root);
  let changes = 0;
  const marked = automatic ? { ...properties, [automatic]: { ...properties[automatic], automatic: true } } : properties;
  const fields = settingsFields(root, "Options", { properties: marked }, defaults, saved, () => { changes++; });
  const one = <T extends Element>(selector: string) => root.querySelector<T>(selector)!;
  const control = (key: string) => one<HTMLElement>('[data-setting="' + key + '"]');
  const error = (key: string) => one<HTMLElement>('[data-setting-error="' + key + '"]');
  const fire = (el: Element, type = "input") => el.dispatchEvent(new Event(type, { bubbles: true }));
  const type = (el: HTMLInputElement, value: string) => { el.value = value; fire(el); };
  return { root, fields, one, control, error, fire, type, changes: () => changes };
}
const rows = (m: ReturnType<typeof mount>, key: string) => [...m.control(key).querySelectorAll<HTMLInputElement>("input[type=number]")];
const button = (m: ReturnType<typeof mount>, text: string) => [...m.root.querySelectorAll<HTMLButtonElement>("button")].find(b => (b.getAttribute("aria-label") ?? b.textContent) === text)!;

it("settingsControls boolean is an accessible checkbox that returns typed booleans", () => {
  const m = mount({ rich: { type: "boolean", title: "Rich start", description: "Players begin with extra money.", default: false } });
  expect(m.root.querySelector("select")).toBeNull();
  const box = m.control("rich") as HTMLInputElement;
  expect(box.type).toBe("checkbox");
  expect(box.getAttribute("aria-label")).toBe("Rich start");
  expect(box.closest("label")!.textContent).toContain("Players begin with extra money.");
  expect(box.closest("label")!.title).toBe("Players begin with extra money.");
  expect(box.checked).toBe(false);
  box.click();
  expect(m.fields.read()).toEqual({ rich: true });
  box.click();
  expect(m.fields.read()).toEqual({ rich: false });
});

it("settingsControls untouched boolean keeps the saved value and explicit false is false", () => {
  const m = mount({ rich: { type: "boolean", title: "Rich start", default: true } }, {}, { rich: false, legacy: 1 });
  expect((m.control("rich") as HTMLInputElement).checked).toBe(false);
  expect(m.fields.read()).toEqual({ rich: false, legacy: 1 });
});

it("settingsControls enum options get generic human labels while transport values stay exact", () => {
  const m = mount({
    style: { type: "string", title: "Style", enum: ["fast_mode", "careful"] },
    level: { type: "integer", title: "Level", enum: [1, 2, 3] },
    flag: { title: "Flag choice", enum: [false, true] },
    named: { type: "string", title: "Named", enum: ["a", "b"], enumLabels: { a: "Alpha rules", b: "Beta rules" } },
  }, { style: "careful", level: 1, flag: true, named: "a" });
  const options = (key: string) => [...(m.control(key) as HTMLSelectElement).options].map(o => o.textContent);
  expect(options("style")).toEqual(["Fast mode", "Careful"]);
  expect(options("named")).toEqual(["Alpha rules", "Beta rules"]);
  expect(m.root.querySelector("select[data-setting=flag]")).toBeTruthy();
  const pick = (key: string, value: string) => { const s = m.control(key) as HTMLSelectElement; s.value = value; m.fire(s, "change"); };
  pick("style", "fast_mode"); pick("level", "2"); pick("flag", "false"); pick("named", "b");
  expect(m.fields.read()).toEqual({ style: "fast_mode", level: 2, flag: false, named: "b" });
});

it("settingsControls unknown enum value is kept, shown as an issue and never coerced", () => {
  const m = mount({ style: { type: "string", title: "Style", enum: ["standard", "careful"] } }, {}, { style: "retired_style" });
  const select = m.control("style") as HTMLSelectElement;
  expect(select.selectedOptions[0].textContent).toContain("retired_style");
  expect(m.error("style").textContent).toContain("retired_style");
  expect(m.fields.read()).toEqual({ style: "retired_style" });
  select.value = "careful";
  m.fire(select, "change");
  expect(m.fields.read()).toEqual({ style: "careful" });
  expect(m.error("style").textContent).toBe("");
});

it("settingsControls enum array uses named checkboxes and returns the actual array", () => {
  const m = mount({ modes: { type: "array", title: "Allowed modes", items: { type: "string", enum: ["quick_play", "ranked"] }, default: ["ranked"] } });
  expect(m.root.querySelector("textarea")).toBeNull();
  const boxes = [...m.control("modes").querySelectorAll<HTMLInputElement>("input[type=checkbox]")];
  expect(boxes.map(b => b.getAttribute("aria-label"))).toEqual(["Quick play", "Ranked"]);
  expect(boxes.map(b => b.checked)).toEqual([false, true]);
  boxes[0].click();
  expect(m.fields.read()).toEqual({ modes: ["quick_play", "ranked"] });
  boxes[1].click();
  expect(m.fields.read()).toEqual({ modes: ["quick_play"] });
});

it("settingsControls enum array explains minItems and refuses an invalid selection", () => {
  const m = mount({ modes: { type: "array", title: "Allowed modes", minItems: 1, items: { type: "string", enum: ["a", "b"] } } }, {}, { modes: ["a"] });
  const first = m.control("modes").querySelector<HTMLInputElement>("input")!;
  first.click();
  expect(m.error("modes").textContent).toMatch(/at least 1/i);
  expect(() => m.fields.read()).toThrow("Allowed modes");
  first.click();
  expect(m.error("modes").textContent).toBe("");
  expect(m.fields.read()).toEqual({ modes: ["a"] });
});

it("settingsControls enum array keeps unknown imported values visible until explicitly unchecked", () => {
  const m = mount({ modes: { type: "array", title: "Allowed modes", items: { type: "string", enum: ["a", "b"] } } }, {}, { modes: ["a", "old"] });
  expect(m.fields.read()).toEqual({ modes: ["a", "old"] });
  expect(m.error("modes").textContent).toContain("old");
  const boxes = [...m.control("modes").querySelectorAll<HTMLInputElement>("input[type=checkbox]")];
  expect(boxes.length).toBe(3);
  boxes[1].click();
  expect(m.fields.read()).toEqual({ modes: ["a", "b", "old"] });
  boxes[2].click();
  expect(m.fields.read()).toEqual({ modes: ["a", "b"] });
  expect(m.error("modes").textContent).toBe("");
});

it("settingsControls numeric array has repeatable rows, keeps order and accepts zero, decimals and the inclusive minimum", () => {
  const m = mount({ bonuses: { type: "array", title: "Station bonuses", minItems: 1, items: { type: "number", minimum: 0 }, default: [2, 0.5] } });
  expect(m.root.querySelector("textarea")).toBeNull();
  expect(rows(m, "bonuses").map(r => r.value)).toEqual(["2", "0.5"]);
  button(m, "Add Station bonuses value").click();
  const added = rows(m, "bonuses");
  expect(added.length).toBe(3);
  m.type(added[2], "0");
  m.type(added[0], "1.25");
  expect(m.fields.read()).toEqual({ bonuses: [1.25, 0.5, 0] });
  expect(rows(m, "bonuses").every(r => r.checkValidity())).toBe(true);
});

it("settingsControls numeric array rows can be removed and the control stays focused sensibly", () => {
  const m = mount({ bonuses: { type: "array", title: "Station bonuses", items: { type: "number" }, default: [1, 2, 3] } });
  button(m, "Remove Station bonuses value 2").click();
  expect(m.fields.read()).toEqual({ bonuses: [1, 3] });
  expect(rows(m, "bonuses").map(r => r.getAttribute("aria-label"))).toEqual(["Station bonuses value 1", "Station bonuses value 2"]);
  expect(document.activeElement).toBe(rows(m, "bonuses")[1]);
  for (const b of m.root.querySelectorAll("button")) expect(b.type).toBe("button");
});

it("settingsControls numeric array rejects blank, non-numeric, below-minimum and too-few rows with field errors", () => {
  const m = mount({ bonuses: { type: "array", title: "Station bonuses", minItems: 2, items: { type: "number", minimum: 0 }, default: [1, 2] } });
  const [a, b] = rows(m, "bonuses");
  m.type(a, "-0.1");
  expect(a.checkValidity()).toBe(false);
  expect(m.error("bonuses").textContent).toMatch(/0/);
  expect(() => m.fields.read()).toThrow("Station bonuses");
  m.type(a, "abc");
  expect(() => m.fields.read()).toThrow("Station bonuses");
  m.type(a, "");
  expect(a.checkValidity()).toBe(false);
  m.type(a, "0");
  expect(m.fields.read()).toEqual({ bonuses: [0, 2] });
  button(m, "Remove Station bonuses value 2").click();
  expect(m.error("bonuses").textContent).toMatch(/at least 2/i);
  expect(() => m.fields.read()).toThrow("Station bonuses");
  void b;
});

it("settingsControls numeric array enforces integer item types", () => {
  const m = mount({ steps: { type: "array", title: "Steps", items: { type: "integer" }, default: [1] } });
  m.type(rows(m, "steps")[0], "1.5");
  expect(() => m.fields.read()).toThrow("Steps");
  m.type(rows(m, "steps")[0], "3");
  expect(m.fields.read()).toEqual({ steps: [3] });
});

it("settingsControls array without a supported item type is preserved and explained as an advanced fallback", () => {
  const saved = { things: [{ a: 1 }, "x"] };
  const m = mount({ things: { type: "array", title: "Things" } }, {}, saved);
  expect(m.root.querySelectorAll("button").length).toBe(0);
  expect(m.control("things").closest("label")!.textContent).toMatch(/no form editor/i);
  expect(m.fields.read()).toEqual(saved);
  const area = m.control("things") as HTMLTextAreaElement;
  m.type(area as unknown as HTMLInputElement, "[1,");
  expect(area.checkValidity()).toBe(false);
  expect(m.error("things").textContent).toMatch(/valid JSON list/);
  expect(() => m.fields.read()).toThrow("Things");
  m.type(area as unknown as HTMLInputElement, "[2]");
  expect(m.fields.read()).toEqual({ things: [2] });
});

it("settingsControls numeric array with non-numeric imported items is preserved, not rewritten into rows", () => {
  const saved = { bonuses: [1, "two"] };
  const m = mount({ bonuses: { type: "array", title: "Station bonuses", items: { type: "number" } } }, {}, saved);
  expect(rows(m, "bonuses").length).toBe(0);
  expect(m.control("bonuses").closest("label")!.textContent).toMatch(/do not fit/i);
  expect(m.fields.read()).toEqual(saved);
});

const select = (m: ReturnType<typeof mount>, key: string) => m.control(key) as HTMLSelectElement;
const choose = (m: ReturnType<typeof mount>, key: string, index: number) => { const s = select(m, key); s.selectedIndex = index; m.fire(s, "change"); };

it("settingsControls wrong-typed imported enum value stays visibly unrecognized and is never read as the number", () => {
  const m = mount({ level: { type: "integer", title: "Level", enum: [1, 2] } }, {}, { level: "2" });
  const s = select(m, "level");
  expect(s.selectedOptions[0].textContent).toContain("Unrecognized");
  expect(s.selectedOptions[0].textContent).toContain('"2"');
  expect(m.error("level").textContent).toContain('"2"');
  expect(m.fields.read()).toEqual({ level: "2" });
  m.fire(s, "change");
  expect(m.fields.read()).toEqual({ level: "2" });
  expect(typeof (m.fields.read() as any).level).toBe("string");
  choose(m, "level", [...s.options].findIndex(o => o.textContent === "2"));
  expect((m.fields.read() as any).level).toBe(2);
  expect(m.error("level").textContent).toBe("");
});

it("settingsControls enum values with the same string form keep distinct typed identities", () => {
  const m = mount({
    mixed: { title: "Mixed", enum: [1, "1"] },
    flags: { title: "Flags", enum: [false, "false", null, "null"] },
  }, { mixed: 1, flags: false });
  const typed = (key: string) => (m.fields.read() as any)[key];
  choose(m, "mixed", 1);
  expect(typed("mixed")).toBe("1");
  choose(m, "mixed", 0);
  expect(typed("mixed")).toBe(1);
  expect(new Set([...select(m, "flags").options].map(o => o.value)).size).toBe(4);
  choose(m, "flags", 1);
  expect(typed("flags")).toBe("false");
  choose(m, "flags", 2);
  expect(typed("flags")).toBe(null);
  choose(m, "flags", 3);
  expect(typed("flags")).toBe("null");
  choose(m, "flags", 0);
  expect(typed("flags")).toBe(false);
});

it("settingsControls explicit edits of the JSON fallback keep numeric maximum, integer and finite rules", () => {
  const saved = { values: ["bad"] };
  const m = mount({ values: { type: "array", title: "Values", items: { type: "number", minimum: 0, maximum: 5 } } }, {}, saved);
  const area = m.control("values") as HTMLTextAreaElement;
  expect(m.error("values").textContent).toMatch(/Item 1/);
  expect(m.fields.read()).toEqual(saved);
  const edit = (text: string) => m.type(area as unknown as HTMLInputElement, text);
  for (const bad of ["[6]", "[-1]", "[1e400]", '["x"]'])
    { edit(bad); expect(area.checkValidity(), bad).toBe(false); expect(() => m.fields.read(), bad).toThrow("Values"); }
  edit("[0, 5]");
  expect(m.fields.read()).toEqual({ values: [0, 5] });
  const whole = mount({ values: { type: "array", title: "Whole", items: { type: "integer", minimum: 0, maximum: 5 } } }, {}, saved);
  const box = whole.control("values") as HTMLTextAreaElement;
  for (const bad of ["[1.5]", '["still bad"]', "[6]"]) {
    whole.type(box as unknown as HTMLInputElement, bad);
    expect(() => whole.fields.read(), bad).toThrow("Whole");
  }
  whole.type(box as unknown as HTMLInputElement, "[3]");
  expect(whole.fields.read()).toEqual({ values: [3] });
});

it("settingsControls shows an imported issue on the closed group summary and keeps the draft", () => {
  const m = mount({ style: { type: "string", title: "Style", enum: ["a"] } }, {}, { style: "retired" });
  const details = m.one<HTMLDetailsElement>("details");
  expect(details.open).toBe(false);
  expect(details.querySelector("summary")!.textContent).toMatch(/1 field needs attention/);
  expect(m.error("style").textContent).toContain("retired");
  expect(m.fields.read()).toEqual({ style: "retired" });
});

it("settingsControls reopens the group and explains an edited invalid field after it was closed", () => {
  const m = mount({ depth: { type: "integer", title: "Depth", minimum: 1, default: 4 } });
  const details = m.one<HTMLDetailsElement>("details");
  const summary = details.querySelector("summary")!;
  expect(summary.textContent).not.toMatch(/attention/);
  details.open = true;
  m.type(m.control("depth") as HTMLInputElement, "0");
  expect(summary.textContent).toMatch(/1 field needs attention/);
  details.open = false;
  expect(() => m.fields.read()).toThrow("Depth");
  expect(details.open).toBe(true);
  expect(m.error("depth").textContent).toMatch(/at least 1/i);
  m.type(m.control("depth") as HTMLInputElement, "2");
  expect(summary.textContent).not.toMatch(/attention/);
});

it("settingsControls native invalid event opens the closed group", () => {
  const m = mount({ depth: { type: "integer", title: "Depth", minimum: 1 } });
  const details = m.one<HTMLDetailsElement>("details");
  m.fire(m.control("depth"), "invalid");
  expect(details.open).toBe(true);
});

it.each([0, 7])("settingsControls valid saved automatic value %s is shown, not flagged and read exactly until Auto is chosen", saved => {
  const m = mount({ wait: { type: "number", title: "Wait", minimum: 0 } }, {}, { wait: saved, kept: "unknown" }, "wait");
  const input = m.control("wait") as HTMLInputElement;
  expect(input.value).toBe(String(saved));
  expect(input.placeholder).toBe("Auto");
  expect(m.error("wait").textContent).toBe("");
  expect(m.one("summary").textContent).not.toMatch(/attention/);
  expect(input.checkValidity()).toBe(true);
  expect(m.fields.read()).toEqual({ wait: saved, kept: "unknown" });
  m.type(input, "");
  expect(m.fields.read()).toEqual({ kept: "unknown" });
  m.type(input, "4");
  expect(m.fields.read()).toEqual({ wait: 4, kept: "unknown" });
});

it("lobbyFlow automatic comes from the schema hint, not from a field name", () => {
  const m = mount({
    quiet_s: { type: "number", title: "Quiet time", minimum: 0, default: 3, automatic: true },
    plain: { type: "number", title: "Plain time", minimum: 0, default: 3 },
  });
  expect((m.control("quiet_s") as HTMLInputElement).placeholder).toBe("Auto");
  expect(m.control("quiet_s").closest("label")!.textContent).toContain("Default: Auto");
  expect((m.control("plain") as HTMLInputElement).placeholder).toBe("");
  expect((m.control("plain") as HTMLInputElement).value).toBe("3");
  expect(m.fields.read()).toEqual({});
});

it("lobbyFlow a restored explicit automatic value survives a roster re-render and Auto removes only that override", () => {
  const schema = { quiet_s: { type: "number", title: "Quiet time", minimum: 0, default: 3, automatic: true }, rounds: { type: "integer", title: "Rounds", minimum: 0 } } as Record<string, SettingSchema>;
  const root = document.createElement("div");
  document.body.append(root);
  const first = settingsFields(root, "Live", { properties: schema }, {}, { rounds: 0, future_key: [1] });
  const typed = root.querySelector<HTMLInputElement>('[data-setting="quiet_s"]')!;
  typed.value = "7";
  typed.dispatchEvent(new Event("input"));
  const saved = first.read();
  expect(saved).toEqual({ rounds: 0, future_key: [1], quiet_s: 7 });
  root.replaceChildren();
  const again = settingsFields(root, "Live", { properties: schema }, {}, saved);
  const restored = root.querySelector<HTMLInputElement>('[data-setting="quiet_s"]')!;
  expect(restored.value).toBe("7");
  expect(again.read()).toEqual(saved);
  restored.value = "";
  restored.dispatchEvent(new Event("input"));
  expect(again.read()).toEqual({ rounds: 0, future_key: [1] });
  expect(restored.placeholder).toBe("Auto");
});

it.each([
  ["7", "string"], ["0", "string"], [[7], "array"], [[0], "array"], [1.5, "fraction for an integer"], [Infinity, "non-finite"],
])("settingsControls wrong-typed or out-of-schema saved automatic value %j is warned and read exactly (%s)", (bad: unknown, _kind: string) => {
  const schema = typeof bad === "number" && bad === 1.5 ? { type: "integer", title: "Wait", minimum: 0 } : { type: "number", title: "Wait", minimum: 0 };
  const m = mount({ wait: schema }, {}, { wait: bad }, "wait");
  expect(m.error("wait").textContent).not.toBe("");
  expect(m.one("summary").textContent).toMatch(/1 field needs attention/);
  expect((m.control("wait") as HTMLInputElement).checkValidity()).toBe(true);
  expect(m.fields.read()).toEqual({ wait: bad });
});

it("settingsControls saved automatic null is valid only when the schema declares null", () => {
  const nullable = mount({ wait: { type: ["number", "null"], title: "Wait" } }, {}, { wait: null }, "wait");
  expect(nullable.error("wait").textContent).toBe("");
  expect(nullable.fields.read()).toEqual({ wait: null });
  const strict = mount({ wait: { type: "number", title: "Wait" } }, {}, { wait: null }, "wait");
  expect(strict.error("wait").textContent).not.toBe("");
  expect(strict.fields.read()).toEqual({ wait: null });
});

it("settingsControls malformed saved automatic values are still explained and not coerced", () => {
  for (const bad of ["abc", -1, null]) {
    const m = mount({ wait: { type: "number", title: "Wait", minimum: 0 } }, {}, { wait: bad }, "wait");
    expect(m.error("wait").textContent, String(bad)).not.toBe("");
    expect(m.one("summary").textContent).toMatch(/1 field needs attention/);
    expect((m.control("wait") as HTMLInputElement).checkValidity()).toBe(true);
    expect(m.fields.read()).toEqual({ wait: bad });
  }
});

it("settingsControls does not flag untouched blank fields", () => {
  const m = mount({ depth: { type: "integer", title: "Depth", minimum: 1 } });
  expect(m.one("summary").textContent).not.toMatch(/attention/);
  expect(m.error("depth").textContent).toBe("");
});

it("settingsControls shows description, default and permitted range next to a numeric field", () => {
  const m = mount({ depth: { type: "integer", title: "Depth", description: "How far the bot looks ahead.", minimum: 1, maximum: 9, default: 4 } });
  const text = (m.control("depth") as HTMLInputElement).closest("label")!.textContent!;
  expect(text).toContain("How far the bot looks ahead.");
  expect(text).toMatch(/Default:?\s*4/);
  expect(text).toMatch(/1\D+9/);
});

it("settingsControls shows a specific error beside an invalid number and clears it on correction", () => {
  const m = mount({ depth: { type: "integer", title: "Depth", minimum: 1, maximum: 9, default: 4 } });
  const input = m.control("depth") as HTMLInputElement;
  m.type(input, "12");
  expect(input.checkValidity()).toBe(false);
  expect(m.error("depth").textContent).toContain("9");
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect(input.getAttribute("aria-describedby")).toContain(m.error("depth").id);
  m.type(input, "5");
  expect(m.error("depth").textContent).toBe("");
  expect(m.fields.read()).toEqual({ depth: 5 });
});

it("settingsControls keeps automatic and nullable numeric semantics", () => {
  const m = mount({
    wait: { type: "number", title: "Wait", minimum: 0, default: 3 },
    rounds: { type: ["integer", "null"], title: "Rounds", minimum: 1, default: null },
    kept: { type: ["integer", "null"], title: "Kept", minimum: 1 },
    zero: { type: "number", title: "Zero", minimum: 0, default: 5 },
  }, {}, { kept: null }, "wait");
  const input = (k: string) => m.control(k) as HTMLInputElement;
  expect(input("wait").value).toBe("");  // no saved value: blank, the module decides
  expect(input("wait").placeholder).toBe("Auto");
  expect(m.fields.read()).toEqual({ kept: null });
  m.type(input("wait"), "7");
  m.type(input("rounds"), "9");
  m.type(input("zero"), "0");
  expect(m.fields.read()).toEqual({ kept: null, wait: 7, rounds: 9, zero: 0 });
  m.type(input("wait"), "");
  m.type(input("rounds"), "");
  expect(m.fields.read()).toEqual({ kept: null, rounds: null, zero: 0 });
  expect(input("wait").checkValidity()).toBe(true);
});

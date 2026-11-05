import { button, element } from "./dom";
import type { SettingSchema } from "./play-types";
type Values = Record<string, unknown>;
/** A problem with a field. Blocking problems make read() throw; others only inform and keep the draft intact. */
interface Issue { message: string; blocking: boolean; bad?: ControlElement[]; }
type ControlElement = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
let nextId = 0;
/** Generic English label for a transport value; schemas may override it with `enumLabels`. */
function choiceLabel(value: unknown, labels?: Record<string, string>) {
  const custom = labels?.[String(value)];
  if (custom !== undefined)
    return custom;
  if (value === true) return "Yes";
  if (value === false) return "No";
  if (value === null) return "None";
  if (typeof value !== "string") return String(value);
  const words = value.replace(/_+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
/** Strings are quoted so that "2" is never mistaken for the number 2. */
const describeValue = (value: unknown) => typeof value === "object" && value !== null || typeof value === "string" ? JSON.stringify(value) : String(value);
/** Checks one list item against the item types declared by the schema, without guessing undeclared kinds. */
function itemProblem(value: unknown, items?: SettingSchema): string {
  if (!items)
    return "";
  if (items.enum)
    return items.enum.some(o => o === value) ? "" : "is not an allowed choice.";
  const declared = [items.type].flat().filter((t): t is string => !!t);
  if (!declared.length)
    return "";
  const fits = declared.some(t => t === "number" ? typeof value === "number" && Number.isFinite(value) :
    t === "integer" ? typeof value === "number" && Number.isInteger(value) :
      t === "string" ? typeof value === "string" : t === "boolean" ? typeof value === "boolean" : t === "null" ? value === null :
        t === "array" ? Array.isArray(value) : t === "object" ? typeof value === "object" && value !== null && !Array.isArray(value) : true);
  if (!fits)
    return typeof value === "number" && declared.some(t => t === "number" || t === "integer")
      ? (Number.isFinite(value) ? "must be a whole number." : "must be a finite number.") : "has the wrong type.";
  if (typeof value === "number") {
    if (items.minimum !== undefined && value < items.minimum)
      return "must be at least " + items.minimum + ".";
    if (items.maximum !== undefined && value > items.maximum)
      return "must be at most " + items.maximum + ".";
  }
  return "";
}
const plural = (n: number, word: string) => n + " " + word + (n === 1 ? "" : "s");
function defaultText(field: SettingSchema, types: unknown[], value: unknown, automatic: boolean) {
  if (automatic) return "Auto";
  if (value === undefined) return "";
  if (value === null) return types.includes("null") ? "Unlimited" : "None";
  if (typeof value === "boolean" && !field.enum) return value ? "On" : "Off";
  if (Array.isArray(value))
    return value.length ? value.map(v => choiceLabel(v, field.items?.enumLabels)).join(", ") : "none";
  return field.enum ? choiceLabel(value, field.enumLabels) : String(value);
}
function rangeText(minimum?: number, maximum?: number, each = false) {
  const prefix = each ? "Each value " : "";
  if (minimum !== undefined && maximum !== undefined) return prefix + (each ? "range: " : "Range: ") + minimum + "-" + maximum;
  // A bound just above zero (the smallest positive float) means "more than 0", not a number to read.
  if (minimum !== undefined && minimum > 0 && minimum < 1e-9) return prefix + (each ? "more than 0" : "More than 0");
  if (minimum !== undefined) return prefix + (each ? "minimum: " : "Minimum: ") + minimum;
  if (maximum !== undefined) return prefix + (each ? "maximum: " : "Maximum: ") + maximum;
  return "";
}
/**
 * A field keeps the distinction between a module default and an explicit unlimited value.
 * `compact` lays the fields out as one row of labelled controls (the "New table" form): no group heading, no
 * Default/Range notes (the description is the tooltip), suggested `presets` as a select, an Auto field shows its default.
 */
export function settingsFields(parent: HTMLElement, title: string, schema?: SettingSchema, defaults: Values = {}, saved: Values = {}, changed = () => { }, compact = false) {
  const group = element(compact ? "div" : "details", compact ? "bc-setting-group bc-setting-row-compact" : "bc-setting-group") as HTMLDetailsElement;
  const summary = element("summary", "", title);
  const flag = element("span", "bc-setting-flag");
  summary.append(flag);
  if (!compact) group.append(summary);
  // Fields with a visible issue; the summary says so even while the group is closed.
  const flagged = new Set<string>();
  const refreshFlag = () => {
    flag.textContent = flagged.size ? " - " + plural(flagged.size, "field") + (flagged.size === 1 ? " needs" : " need") + " attention" : "";
  };
  const readers = new Map<string, () => unknown>();
  const touched = new Set<string>();
  // Automatic fields the user deliberately blanked: the saved override must leave the request, not linger in it.
  const cleared = new Set<string>();
  for (const [key, field] of Object.entries(schema?.properties ?? {})) {
    const name = field.title ?? key;
    const types = [field.type].flat(), nullable = types.includes("null");
    const initial = key in saved ? saved[key] : key in defaults ? defaults[key] : field.default;
    const automatic = field.automatic === true;
    const isArray = types.includes("array") && !types.includes("boolean") && !field.enum;
    const list = Array.isArray(initial) ? initial : initial === undefined ? [] : undefined;
    const numericItems = [field.items?.type].flat().some(t => t === "number" || t === "integer");
    // Lists whose item type or saved data has no safe form editor fall back to an explained JSON box.
    const arrayKind = !isArray ? "" : list && field.items?.enum ? "choices" : list && numericItems && list.every(v => typeof v === "number") ? "rows" : "json";
    const grouped = arrayKind === "choices" || arrayKind === "rows";
    const wrap: HTMLElement = element(grouped ? "fieldset" : "label", "bc-setting");
    const error = element("small", "bc-setting-error");
    error.id = "bc-setting-error-" + ++nextId;
    error.dataset.settingError = key;
    error.setAttribute("role", "alert");
    let controls: () => ControlElement[] = () => [];
    let check: () => Issue | null = () => null;
    let initialCheck: (() => Issue | null) | undefined;
    let value: () => unknown = () => initial;
    let primary: HTMLElement | undefined;
    // Auto-fields stay blank until the user types, so the module default is never frozen into the request.
    // A well-typed saved override is shown, and blanking the input again returns to Auto.
    let blankResets = false;
    const heading = grouped ? element("legend", "bc-setting-title", name) : element("span", "bc-setting-title", name);
    const itemTypes = [field.items?.type].flat();
    const metaParts: string[] = [];
    const defaultValue = key in defaults ? defaults[key] : field.default;
    const shownDefault = defaultText(field, types, defaultValue, automatic);
    if (shownDefault)
      metaParts.push("Default: " + shownDefault);
    let edited = () => { };
    const bind = (control: Element) => {
      (control as HTMLElement).oninput = (control as HTMLElement).onchange = () => edited();
      // A browser cannot focus an invalid control inside a closed group, so reveal it.
      control.addEventListener("invalid", () => { group.open = true; });
    };
    const presets = compact ? field.presets : undefined;
    if (presets?.length && !(key in saved && !presets.some(p => p.value === saved[key]))) {
      // Suggested values only: the server still validates the chosen one against the schema.
      const select = element("select");
      presets.forEach((p, i) => select.append(Object.assign(element("option", "", p.label), { value: String(i) })));
      const at = presets.findIndex(p => p.value === initial);
      select.selectedIndex = at >= 0 ? at : 0;
      controls = () => [select];
      value = () => presets[select.selectedIndex]?.value;
      primary = select;
      wrap.append(heading, select);
    }
    else if (types.includes("boolean")) {
      const box = element("input");
      box.type = "checkbox";
      box.checked = initial === true;
      primary = box;
      controls = () => [box];
      value = () => box.checked;
      wrap.append(box, heading);
    }
    else if (field.enum) {
      const select = element("select");
      const choices = field.enum;
      // Option values stay the plain transport text when that is unambiguous; otherwise they fall back to indexes.
      // Either way the typed value is looked up from the option element, never parsed back from its text.
      const texts = choices.map(String);
      const plain = new Set(texts).size === texts.length;
      const typed = new Map<HTMLOptionElement, unknown>();
      choices.forEach((v, i) => {
        const option = element("option", "", choiceLabel(v, field.enumLabels));
        option.value = plain ? texts[i] : "choice-" + i;
        typed.set(option, v);
        select.append(option);
      });
      const known = choices.findIndex(v => v === initial);
      let kept: HTMLOptionElement | undefined;
      if (known < 0) {
        // Keep an unrecognised or missing value visible instead of silently selecting a supported option.
        kept = element("option", "", initial == null ? "Choose…" : "Unrecognized: " + describeValue(initial));
        let marker = "unrecognized";
        while ([...typed.keys()].some(o => o.value === marker))
          marker += "_";
        kept.value = marker;
        select.prepend(kept);
        select.selectedIndex = 0;
      }
      else
        select.selectedIndex = known;
      const chosen = () => select.options[select.selectedIndex];
      const recognized = () => typed.has(chosen());
      controls = () => [select];
      check = () => recognized() ? null : initial == null
        ? { message: "Choose an option.", blocking: true }
        : { message: "Saved value " + describeValue(initial) + " is not a supported option. Choose a supported option, or leave it for the server to validate.", blocking: false };
      value = () => recognized() ? typed.get(chosen()) : initial;
      primary = select;
      wrap.append(heading, select);
    }
    else if (isArray) {
      const items = field.items;
      const minimum = field.minItems ?? 0;
      if (grouped) {
        wrap.setAttribute("aria-label", name);
        wrap.append(heading);
      }
      if (arrayKind === "choices" && list && items?.enum) {
        const options = items.enum;
        const unknown = list.filter(v => !options.some(o => o === v));
        const entries = [...options, ...unknown].map((v, index) => {
          const row = element("label", "bc-setting-choice");
          const box = element("input");
          box.type = "checkbox";
          const text = index < options.length ? choiceLabel(v, items.enumLabels) : "Unrecognized: " + describeValue(v);
          box.checked = list.some(x => x === v);
          box.setAttribute("aria-label", text);
          row.append(box, element("span", "", text));
          return { box, v, known: index < options.length };
        });
        const body = element("div", "bc-setting-choices");
        body.append(...entries.map(e => e.box.parentElement!));
        wrap.append(body);
        const selected = () => entries.filter(e => e.box.checked);
        controls = () => entries.map(e => e.box);
        value = () => [...selected().filter(e => e.known), ...selected().filter(e => !e.known)].map(e => e.v);
        check = () => {
          if (selected().length < minimum)
            return { message: "Select at least " + plural(minimum, "option") + ".", blocking: true, bad: entries.slice(0, 1).map(e => e.box) };
          const stale = selected().filter(e => !e.known).map(e => describeValue(e.v));
          return stale.length ? { message: "Saved value(s) not supported: " + stale.join(", ") + ". Uncheck them, or leave them for the server to validate.", blocking: false } : null;
        };
        if (minimum)
          metaParts.push("Select at least " + minimum);
      }
      else if (arrayKind === "rows" && list && items) {
        const whole = itemTypes.includes("integer") && !itemTypes.includes("number");
        const body = element("div", "bc-setting-rows");
        const inputs: HTMLInputElement[] = [];
        const add = button("Add value", () => { addRow(""); inputs[inputs.length - 1].focus(); edited(); }, "bc-setting-add");
        const relabel = () => inputs.forEach((input, i) => {
          input.setAttribute("aria-label", name + " value " + (i + 1));
          const remove = input.parentElement!.querySelector("button")!;
          remove.setAttribute("aria-label", "Remove " + name + " value " + (i + 1));
        });
        const addRow = (text: string) => {
          const row = element("div", "bc-setting-row");
          const input = element("input");
          input.type = "number";
          input.step = whole ? "1" : "any";
          if (items!.minimum !== undefined) input.min = String(items!.minimum);
          if (items!.maximum !== undefined) input.max = String(items!.maximum);
          input.value = text;
          input.dataset.settingItem = key;
          const remove = button("Remove", () => {
            const at = inputs.indexOf(input);
            inputs.splice(at, 1);
            row.remove();
            relabel();
            (inputs[at] ?? inputs[at - 1] ?? add).focus();
            edited();
          }, "bc-setting-remove");
          row.append(input, remove);
          body.append(row);
          inputs.push(input);
          bind(input);
          relabel();
        };
        for (const v of list) addRow(String(v));
        add.setAttribute("aria-label", "Add " + name + " value");
        wrap.append(body, add);
        controls = () => inputs;
        value = () => inputs.map(i => Number(i.value));
        check = () => {
          const bad: HTMLInputElement[] = [];
          let message = "";
          inputs.forEach((input, i) => {
            const n = Number(input.value), at = "Value " + (i + 1) + ": ";
            const problem = !input.value.trim() || input.validity.badInput || !Number.isFinite(n) ? "enter a number." :
              whole && !Number.isInteger(n) ? "enter a whole number." :
                items!.minimum !== undefined && n < items!.minimum ? "must be at least " + items!.minimum + "." :
                  items!.maximum !== undefined && n > items!.maximum ? "must be at most " + items!.maximum + "." : "";
            if (problem) {
              bad.push(input);
              message ||= at + problem;
            }
          });
          if (!message && inputs.length < minimum)
            message = "Add at least " + plural(minimum, "value") + ".";
          return message ? { message, blocking: true, bad } : null;
        };
        const range = rangeText(items.minimum, items.maximum, true);
        if (range) metaParts.push(range);
        if (minimum)
          metaParts.push("At least " + plural(minimum, "value"));
      }
      else {
        // No row or checkbox editor fits this schema or the saved data, so offer the previous JSON box and say so.
        const area = element("textarea");
        area.rows = 2;
        area.value = JSON.stringify(initial ?? []);
        primary = area;
        controls = () => [area];
        const parsed = (): unknown => { try { return JSON.parse(area.value); } catch { return undefined; } };
        check = () => {
          const array = parsed();
          if (!Array.isArray(array))
            return { message: "Enter a valid JSON list.", blocking: true };
          if (array.length < minimum)
            return { message: "Enter at least " + plural(minimum, "item") + ".", blocking: true };
          for (let i = 0; i < array.length; i++) {
            const problem = itemProblem(array[i], items);
            if (problem)
              return { message: "Item " + (i + 1) + " (" + describeValue(array[i]) + ") " + problem, blocking: true };
          }
          return null;
        };
        value = () => parsed();
        metaParts.push(numericItems
          ? "The saved list has values that do not fit its numeric schema, so it is shown as JSON. It is sent unchanged until you edit it."
          : "Advanced: this list type has no form editor, so it is edited as JSON. The saved value is sent unchanged until you edit it.");
        wrap.append(heading, area);
      }
    }
    else {
      const input = element("input");
      input.type = types.includes("string") ? "text" : "number";
      input.step = types.includes("integer") ? "1" : "any";
      if (field.minimum !== undefined)
        input.min = String(field.minimum);
      if (field.maximum !== undefined)
        input.max = String(field.maximum);
      input.placeholder = automatic ? "Auto" : nullable ? "Unlimited" : "";
      // Only a saved override that this field accepts is shown; anything else stays blank, is explained and is sent unchanged.
      const raw = saved[key];
      const restored = automatic && key in saved && (input.type === "number"
        ? typeof raw === "number" && Number.isFinite(raw) && (!types.includes("integer") || types.includes("number") || Number.isInteger(raw)) &&
          (field.minimum === undefined || raw >= field.minimum) && (field.maximum === undefined || raw <= field.maximum)
        : typeof raw === "string");
      input.value = automatic ? (restored ? String(saved[key]) : compact && typeof field.default === "number" ? String(field.default) : "") : initial == null ? "" : String(initial);
      blankResets = automatic;
      controls = () => [input];
      primary = input;
      const checkText = (text: string): Issue | null => {
        if (!text)
          return nullable ? null : { message: "Enter a value.", blocking: true };
        if (input.type !== "number")
          return null;
        const n = Number(text);
        const problem = input.validity.badInput || !Number.isFinite(n) ? "Enter a number." :
          types.includes("integer") && !types.includes("number") && !Number.isInteger(n) ? "Enter a whole number." :
            field.minimum !== undefined && n < field.minimum ? "Must be at least " + field.minimum + "." :
              field.maximum !== undefined && n > field.maximum ? "Must be at most " + field.maximum + "." : "";
        return problem ? { message: problem, blocking: true } : null;
      };
      check = () => checkText(input.value);
      // The Auto input is blank on purpose, so judge a saved Auto value itself rather than the blank display.
      // Judge the saved value by its real type: numeric-looking strings and arrays are not numbers.
      if (automatic && key in saved)
        initialCheck = () => {
          const raw = saved[key];
          if (raw === null)
            return checkText("");
          if (typeof raw !== (input.type === "number" ? "number" : "string"))
            return { message: "Saved value " + describeValue(raw) + " has the wrong type for this field.", blocking: true };
          return checkText(String(raw));
        };
      value = () => nullable && !input.value ? null : input.type === "number" ? Number(input.value) : input.value;
      const range = rangeText(field.minimum, field.maximum);
      if (range) metaParts.push(range);
      wrap.append(heading, input);
    }
    if (field.description) {
      wrap.title = field.description;
      if (!compact) heading.after(element("small", "bc-setting-description", field.description));
    }
    if (compact && field.order !== undefined) wrap.style.order = String(field.order);
    if (metaParts.length && !compact)
      wrap.append(element("small", "bc-setting-meta", metaParts.join(" · ")));
    wrap.append(error);
    for (const holder of [primary, grouped ? wrap : undefined]) {
      if (!holder)
        continue;
      holder.setAttribute("aria-describedby", error.id);
      holder.dataset.setting = key;
    }
    primary?.setAttribute("aria-label", name);
    // `enforce` false explains an imported problem without making the browser block the untouched draft.
    const show = (issue: Issue | null, enforce = true) => {
      error.textContent = issue?.message ?? "";
      for (const control of controls()) {
        const invalid = !!issue?.blocking && (!issue.bad?.length || issue.bad.includes(control));
        control.setCustomValidity(invalid && enforce ? issue!.message : "");
        invalid ? control.setAttribute("aria-invalid", "true") : control.removeAttribute("aria-invalid");
      }
      issue?.blocking ? wrap.setAttribute("aria-invalid", "true") : wrap.removeAttribute("aria-invalid");
      issue ? flagged.add(key) : flagged.delete(key);
      refreshFlag();
    };
    edited = () => {
      const input = primary as HTMLInputElement | undefined;
      if (blankResets && input && !input.value && !input.validity.badInput) {
        touched.delete(key);
        cleared.add(key);
      }
      else {
        touched.add(key);
        cleared.delete(key);
      }
      show(touched.has(key) ? check() : null);
      changed();
    };
    for (const control of controls())
      bind(control);
    const first = (initialCheck ?? check)();
    // Imported problems are explained at once; a blank field the user has not been asked to fill is not.
    if (first && (!first.blocking || key in saved))
      show(first, false);
    readers.set(key, () => {
      const issue = check();
      if (issue?.blocking) {
        show(issue);
        group.open = true;
        throw Error("Check " + name + ": " + issue.message);
      }
      return value();
    });
    group.append(wrap);
  }
  if (readers.size)
    parent.append(group);
  return { read(): Values {
      // Keep the imported draft intact for server validation. Only explicit edits replace its values.
      const result: Values = { ...defaults, ...saved };
      for (const [key, read] of readers)
        if (touched.has(key))
          result[key] = read();
      for (const key of cleared)
        delete result[key];
      return result;
    } };
}
export function selection(id: string, options: Values) { return Object.keys(options).length ? { choice_id: id, options } : id; }

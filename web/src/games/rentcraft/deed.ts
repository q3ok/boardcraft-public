// Title deed with the current rent row highlighted, and the bar with the owner of a space (the original Rentcraft look).
import { ANIMAL_EMOJI, esc, groupClass, groupMembers, money, seatAnimal, seatClass } from "./format";
import type { RentBoard, RentState } from "./types";

const OWNABLE = new Set(["property", "railroad", "utility"]);

function nowBox(owner: number | null, mortgaged: boolean, amount: string | null): string {
  if (owner == null) return `<div class="nowbox free">Unowned - can be bought from the bank</div>`;
  if (mortgaged) return `<div class="nowbox mort"><b>Mortgaged</b> - landing here is free</div>`;
  return `<div class="nowbox">Rent right now: <b>${amount ?? "-"}</b></div>`;
}

function rows(list: [string, string][], active: number): string {
  return list.map(([a, b], k) => `<tr class="${k === active ? "now" : ""}"><td>${a}</td><td>${b}${k === active ? " ◀" : ""}</td></tr>`).join("");
}

/** The printed deed of one space. `names` are the seat names (the owner line). */
export function deedHtml(board: RentBoard, state: RentState, index: number, names: string[]): string {
  const b = board.spaces[index];
  const owner = state.owner?.[index] ?? null;
  const mortgaged = !!state.mortgaged?.[index];
  const level = state.buildings?.[index] ?? 0;
  const ownerLine = `Owner: ${owner == null ? "bank" : esc(names[owner] ?? `Player ${owner + 1}`)}`;
  if (b.kind === "property") {
    const r = b.rents;
    let active = -1;
    if (owner != null && !mortgaged) {
      const full = !!b.group && groupMembers(board, b.group).every(i => state.owner[i] === owner);
      active = level > 0 ? level + 1 : full ? 1 : 0;
    }
    const list: [string, string][] = [
      ["Rent", money(r[0])],
      ["With full colour set", money(r[0] * 2)],
      ["With 1 house", money(r[1])],
      ["With 2 houses", money(r[2])],
      ["With 3 houses", money(r[3])],
      ["With 4 houses", money(r[4])],
      ["With hotel", money(r[5])],
    ];
    return `<div class="deed"><div class="hd ${groupClass(b.group)}"><small>Title deed</small>${esc(b.name)}</div><div class="price">Price ${money(b.price)}</div>
      <table>${rows(list, active)}</table>${nowBox(owner, mortgaged, active >= 0 ? list[active][1] : null)}
      <div class="ft">House ${money(b.house_cost)} · Hotel ${money(b.house_cost)} + 4 houses · Mortgage ${money(b.mortgage_value ?? b.price / 2)}<br>${ownerLine}</div></div>`;
  }
  if (b.kind === "railroad" || b.kind === "utility") {
    const same = board.spaces.filter(s => s.kind === b.kind && owner != null && state.owner[s.index] === owner).length;
    const active = owner != null && !mortgaged ? same - 1 : -1;
    const list: [string, string][] = b.kind === "railroad"
      ? (board.station_rents ?? []).map((v, k) => [`${k + 1} station${k === 0 ? "" : "s"}`, money(v)])
      : (board.utility_multipliers ?? []).map((v, k) => [`${k + 1} utilit${k === 0 ? "y" : "ies"}`, `${v} × dice total`]);
    const label = b.kind === "railroad" ? "🚂 Station" : (index === 12 ? "💡" : "🚰") + " Utility";
    return `<div class="deed"><div class="hd ${groupClass("station")}"><small>${label}</small>${esc(b.name)}</div>
      <div class="price">Price ${money(b.price)}</div><div class="sub">Rent when the owner holds:</div>
      <table>${rows(list, active)}</table>${nowBox(owner, mortgaged, active >= 0 ? list[active]?.[1] ?? null : null)}
      <div class="ft">Mortgage ${money(b.mortgage_value ?? b.price / 2)}<br>${ownerLine}</div></div>`;
  }
  const info: Record<string, string> = {
    opportunity: "Draw an Opportunity card and do what it says.",
    community_fund: "Draw a Community Fund card and do what it says.",
    tax: `Pay ${money(b.tax ?? 0)} tax to the bank.`,
    start: `Passing or landing here collects ${money(board.start_salary ?? 200)}.`,
    jail: "Just visiting - unless you are in jail.",
    free_parking: "Nothing happens. Take a rest.",
    go_to_jail: "Go straight to jail. Do not pass Start.",
  };
  return `<div class="deed"><div class="hd ${groupClass("special")}">${esc(b.name)}</div><div class="ft big">${info[b.kind] ?? ""}</div></div>`;
}

/** Bar with the owner of the space: the player's colour and animal, or "bank". Empty for spaces that cannot be bought. */
export function ownerBadge(board: RentBoard, state: RentState, space: number, names: string[], me: number | null = null): string {
  if (!OWNABLE.has(board.spaces[space].kind)) return "";
  const owner = state.owner?.[space] ?? null;
  if (owner == null) return `<div class="owner-badge bank">🏦 Owner: bank - unowned</div>`;
  const who = owner === me ? "You" : esc(names[owner] ?? `Player ${owner + 1}`);
  return `<div class="owner-badge ${seatClass(owner)}"><span class="d"></span>${ANIMAL_EMOJI[seatAnimal(owner)]} Owner: <b>${who}</b></div>`;
}

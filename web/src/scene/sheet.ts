import {
  type BoardSpec,
  type Sheet,
  type Area,
  type Action,
  type Target,
  COLORS,
} from "../games/dice-circuit/types";
import { sameTarget } from "../model";
import { emblemSource } from "../emblem";
export const PAPER_W = 1800,
  PAPER_H = 2800;
export interface Cell {
  area: Area;
  target: Target;
  x: number;
  y: number;
  w: number;
  h: number;
  printed: number | string | null;
}
export interface PrintedReward {
  kind: string;
  source: string;
  x: number;
  y: number;
}
export function sheetCells(spec: BoardSpec): Cell[] {
  const cells: Cell[] = [];
  spec.yellow.rows.forEach((row, r) =>
    row.forEach((n, c) =>
      cells.push({
        area: "yellow",
        target: [r, c],
        x: 120 + c * 142,
        y: 1105 + r * 142,
        w: 125,
        h: 125,
        printed: n,
      }),
    ),
  );
  spec.blue.rows.forEach((row, r) =>
    row.forEach((n, c) => {
      if (n !== null)
        cells.push({
          area: "blue",
          target: n,
          x: 960 + c * 143,
          y: 1230 + r * 143,
          w: 126,
          h: 126,
          printed: n,
        });
    }),
  );
  for (const area of ["green", "orange", "purple"] as Area[]) {
    const y = { green: 1860, orange: 2160, purple: 2420 }[
      area as "green" | "orange" | "purple"
    ];
    const length =
      area === "purple"
        ? spec.purple.length
        : area === "green"
          ? spec.green.thresholds.length
          : spec.orange.multipliers.length;
    for (let i = 0; i < length; i++)
      cells.push({
        area,
        target: i,
        x: 130 + i * 139,
        y: y + (area === "green" ? 120 : 70),
        w: 125,
        h: area === "green" ? 86 : 100,
        printed:
          area === "green"
            ? `≥${spec.green.thresholds[i]}`
            : area === "orange" && spec.orange.multipliers[i] > 1
              ? `×${spec.orange.multipliers[i]}`
              : "",
      });
  }
  return cells;
}
export function sheetRewards(spec: BoardSpec): PrintedReward[] {
  const out: PrintedReward[] = [];
  spec.yellow.rowBonuses.forEach((kind, i) =>
    out.push({ kind, source: `yellow:row:${i}`, x: 804, y: 1167 + i * 142 }),
  );
  out.push({
    kind: spec.yellow.mainDiagonalBonus,
    source: "yellow:diagonal",
    x: 804,
    y: 1740,
  });
  spec.blue.rowBonuses.forEach((kind, i) =>
    out.push({ kind, source: `blue:row:${i}`, x: 1647, y: 1293 + i * 143 }),
  );
  spec.blue.columnBonuses.forEach((kind, i) =>
    out.push({ kind, source: `blue:column:${i}`, x: 1023 + i * 143, y: 1744 }),
  );
  for (const area of ["green", "orange", "purple"] as const)
    for (const [p, kind] of Object.entries(spec[area].bonusesByPosition1Based))
      out.push({
        kind,
        source: `${area}:position:${p}`,
        x: 192 + (Number(p) - 1) * 139,
        y: { green: 2096, orange: 2356, purple: 2616 }[area],
      });
  return out;
}
export function cellAt(spec: BoardSpec, x: number, y: number) {
  return sheetCells(spec).find(
    (c) => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h,
  );
}
export function bonusName(kind: string) {
  return (
    (
      {
        reroll: "Reroll",
        extra: "Extra die +1",
        endgame: "Emblem",
        blackX: "Black cross",
        black6: "Black 6",
        yellowX: "Yellow cross",
        blueX: "Blue cross",
        greenX: "Green cross",
        orange4: "Orange 4",
        orange5: "Orange 5",
        orange6: "Orange 6",
        purple6: "Purple 6",
      } as Record<string, string>
    )[kind] ?? "Bonus"
  );
}
/** An unplayed sheet as printed from the board spec: only the pre-marked yellow cells, nothing earned. */
export function blankSheet(spec: BoardSpec): Sheet {
  return {
    yellow: spec.yellow.premarked.map((cell) => [...cell]),
    blue: [],
    green: 0,
    orange: [],
    purple: [],
    earned_rewards: [],
    rerolls: 0,
    rerolls_earned: 0,
    rerolls_used: 0,
    extras: 0,
    extras_earned: 0,
    extras_used: 0,
    emblem_count: 0,
  };
}
export function drawSheet(
  canvas: HTMLCanvasElement,
  spec: BoardSpec,
  sheet: Sheet,
  name: string,
  round: number,
  actions: Action[] = [],
) {
  canvas.width = PAPER_W;
  canvas.height = PAPER_H;
  const g = canvas.getContext("2d")!;
  const box = (
    x: number,
    y: number,
    w: number,
    h: number,
    fill = "#fffdf5",
    stroke = "#80917d",
    radius = 9,
  ) => {
    g.fillStyle = fill;
    g.strokeStyle = stroke;
    g.lineWidth = 3;
    g.beginPath();
    g.roundRect(x, y, w, h, radius);
    g.fill();
    g.stroke();
  };
  const text = (
    v: unknown,
    x: number,
    y: number,
    size = 40,
    color = "#263c30",
    align: CanvasTextAlign = "center",
    weight = 600,
  ) => {
    g.fillStyle = color;
    g.textAlign = align;
    g.textBaseline = "middle";
    g.font = `${weight} ${size}px Arial, sans-serif`;
    g.fillText(String(v), x, y);
  };
  const cross = (x: number, y: number, size: number, color = "#374959") => {
    g.strokeStyle = color;
    g.lineWidth = 7;
    g.lineCap = "round";
    g.beginPath();
    g.moveTo(x - size, y - size + 3);
    g.lineTo(x + size, y + size);
    g.moveTo(x + size - 3, y - size);
    g.lineTo(x - size + 2, y + size);
    g.stroke();
  };
  g.fillStyle = "#faf8e9";
  g.fillRect(0, 0, PAPER_W, PAPER_H);
  g.strokeStyle = "#a5b19b";
  g.lineWidth = 5;
  g.strokeRect(28, 28, 1744, 2744);
  g.strokeStyle = "#d1d4c3";
  g.lineWidth = 2;
  g.strokeRect(42, 42, 1716, 2716);
  text("BOARDCRAFT", 90, 100, 50, "#28543a", "left", 700);
  text("DICE CIRCUIT · BASE SHEET", 1710, 100, 31, "#536752", "right");
  text(name, 90, 160, 34, "#536752", "left");
  for (let i = 0; i < 3; i++) {
    box(90, 205 + i * 260, 245, 245, "#edf0e8");
    text(i + 1, 117, 232 + i * 260, 24, "#536752");
  }
  const rounds = spec.rounds;
  for (let i = 0; i < rounds.length; i++) {
    const x = 395 + i * 216;
    box(x, 205, 202, 200, i + 1 === round ? "#e7dcab" : "#e7ecdf");
    text(i + 1, x + 22, 232, 27, "#536752");
    text(
      (
        {
          reroll: "↻",
          extra: "+1",
          "blackX-or-black6": "× / 6",
          "players-1-3": "1-3 players",
          "players-1-2": "1-2 players",
        } as Record<string, string>
      )[rounds[i]] ?? rounds[i],
      x + 101,
      302,
      i > 3 ? 27 : 48,
    );
  }
  // Paper tracks: box i is empty, marked (earned, available) or crossed out (used; the oldest marked boxes go first).
  ["Rerolls", "Extra dice +1"].forEach((label, k) => {
    const y = 460 + k * 260;
    const earned = k === 0 ? sheet.rerolls_earned : sheet.extras_earned;
    const used = k === 0 ? sheet.rerolls_used : sheet.extras_used;
    box(395, y, 1272, 180, "#dfe8d8");
    text(label, 420, y + 90, 32, "#294732", "left");
    for (let i = 0; i < spec.resourceSlots; i++) {
      const x = 810 + i * 116;
      g.fillStyle = i < used ? "#a9bba9" : i < earned ? "#3c6948" : "#fff";
      g.strokeStyle = "#869984";
      g.lineWidth = 3;
      g.beginPath();
      g.arc(x, y + 90, 36, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      if (i < earned) text(k === 0 ? "↻" : "+1", x, y + 90, 30, "#fff");
      if (i < used) cross(x, y + 90, 24);
    }
  });
  const panel = (area: Area, x: number, y: number, w: number, h: number) => {
    box(x, y, w, h, COLORS[area] + "40", COLORS[area]);
    g.fillStyle = COLORS[area];
    g.beginPath();
    g.roundRect(x, y, w, 57, [9, 9, 0, 0]);
    g.fill();
    text(
      area.toUpperCase(),
      x + 20,
      y + 29,
      35,
      area === "yellow" ? "#4d430e" : "#fff",
      "left",
      700,
    );
  };
  panel("yellow", 90, 1025, 780, 790);
  panel("blue", 930, 1025, 780, 790);
  panel("green", 90, 1860, 1620, 265);
  panel("orange", 90, 2160, 1620, 225);
  panel("purple", 90, 2420, 1620, 225);
  g.strokeStyle = "#c49b35";
  g.lineWidth = 7;
  g.beginPath();
  g.moveTo(182, 1167);
  g.lineTo(608, 1593);
  g.lineTo(804, 1740);
  g.stroke();
  text("BLUE + WHITE", 960, 1100, 29, "#36544c", "left");
  spec.blue.pointsByCount.slice(1).forEach((v, i) => {
    text(v, 975 + i * 63, 1150, 39);
    text(i + 1, 975 + i * 63, 1195, 29, "#526957");
  });
  for (const c of sheetCells(spec)) {
    const marked =
      c.area === "yellow"
        ? sheet.yellow.some((t) => sameTarget(t, c.target))
        : c.area === "blue"
          ? sheet.blue.includes(c.target as number)
          : c.area === "green"
            ? (c.target as number) < sheet.green
            : (c.target as number) < sheet[c.area].length;
    const legal = actions.some(
      (a) => a.params.area === c.area && sameTarget(a.params.target, c.target),
    );
    box(
      c.x,
      c.y,
      c.w,
      c.h,
      c.printed === null ? "#dce1d2" : legal ? "#fff1a8" : "#fffef7",
      legal ? "#ac7e13" : "#81918a",
    );
    if (legal) {
      g.strokeStyle = "#c79526";
      g.lineWidth = 7;
      g.strokeRect(c.x + 3, c.y + 3, c.w - 6, c.h - 6);
    }
    const value =
      marked && (c.area === "orange" || c.area === "purple")
        ? sheet[c.area][c.target as number]
        : c.printed;
    if (value !== null)
      text(
        value,
        c.x + c.w / 2,
        c.y + c.h / 2,
        c.area === "green" ? 43 : 65,
        c.area === "purple" ? "#633d7e" : "#354544",
      );
    if (
      (marked &&
        (c.area === "yellow" || c.area === "blue" || c.area === "green")) ||
      c.printed === null
    )
      cross(c.x + c.w / 2, c.y + c.h / 2, c.area === "green" ? 29 : 39);
    if (c.area === "green")
      text(
        spec.green.pointsByCount[(c.target as number) + 1],
        c.x + c.w / 2,
        1951,
        40,
      );
    if (c.area === "purple" && (c.target as number) < spec.purple.length - 1)
      text("<", c.x + c.w + 7, c.y + 50, 30, "#754e93");
  }
  spec.yellow.columnPoints.forEach((v, i) => text(v, 182 + i * 142, 1735, 47));
  const emblem = emblemSource();
  const reward = (kind: string, x: number, y: number, earned: boolean) => {
    const area = kind.replace(/[X0-9]/g, "") as Area;
    const color = COLORS[area] ?? "#3d624a";
    if (kind === "endgame") {
      if (emblem.complete && emblem.naturalWidth)
        g.drawImage(emblem, x - 34, y - 34, 68, 68);
    } else {
      box(x - 28, y - 27, 56, 54, color, color, 8);
      text(
        kind === "reroll"
          ? "↻"
          : kind === "extra"
            ? "+1"
            : kind.endsWith("X")
              ? "×"
              : kind.slice(-1),
        x,
        y + 1,
        35,
        "#fff",
      );
    }
    if (earned) {
      g.strokeStyle = "#244a32";
      g.lineWidth = 5;
      g.beginPath();
      g.arc(x, y, 38, 0, Math.PI * 2);
      g.stroke();
    }
  };
  for (const r of sheetRewards(spec))
    reward(r.kind, r.x, r.y, sheet.earned_rewards.includes(r.source));
  if (emblem.complete && emblem.naturalWidth)
    g.drawImage(emblem, 86, 2707, 56, 56);
  text(
    `${sheet.emblem_count} emblems × lowest colour score`,
    151,
    2735,
    33,
    "#536752",
    "left",
    500,
  );
  text("After a 6, any number", 1688, 2449, 29, "#fff", "right", 500);
  text("Dice Circuit", 1710, 2735, 34, "#536752", "right", 500);
}

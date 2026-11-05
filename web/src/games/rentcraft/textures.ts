import * as THREE from "three";
import type { RentBoard } from "./types";
const GROUP_COLOR: Record<string, string> = {
  brown: "#7a4326",
  light_blue: "#9fd8f0",
  pink: "#d43b8a",
  orange: "#ee8a1e",
  red: "#de2027",
  yellow: "#f6dd12",
  green: "#18a253",
  dark_blue: "#1b4fa0",
};
const DARK_TEXT = new Set(["yellow", "light_blue"]);
import { CR, S, W } from "./geometry";
function canvas(w: number, h: number): [
  HTMLCanvasElement,
  CanvasRenderingContext2D
] {
  const cv = document.createElement("canvas");
  cv.width = w;
  cv.height = h;
  return [cv, cv.getContext("2d")!];
}
function tex(cv: HTMLCanvasElement, aniso = 8): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = aniso;
  return t;
}
export function fitFont(g: CanvasRenderingContext2D, text: string, maxW: number, size: number, face: string) {
  do {
    g.font = `bold ${size}px ${face}`;
    size -= 2;
  } while (g.measureText(text).width > maxW && size > 10);
}
function wrap(g: CanvasRenderingContext2D, text: string, x: number, y: number, maxW: number, lh: number): number {
  const words = text.toUpperCase().split(" ");
  const lines: string[] = [];
  let line = "";
  for (const w of words) {
    const t = line ? line + " " + w : w;
    if (g.measureText(t).width > maxW && line) {
      lines.push(line);
      line = w;
    }
    else
      line = t;
  }
  lines.push(line);
  lines.forEach((l, i) => g.fillText(l, x, y + i * lh));
  return lines.length;
}
const NARROW = '"Arial Narrow",Arial,sans-serif';
function drawTile(spec: RentBoard, g: CanvasRenderingContext2D, i: number, x: number, y: number) {
  const b = spec.spaces[i];
  const cx = x + W / 2;
  g.strokeStyle = "#1b1b1b";
  g.lineWidth = 3;
  g.strokeRect(x, y, W, CR);
  g.fillStyle = "#161616";
  g.textAlign = "center";
  g.textBaseline = "top";
  const BAR = CR * 0.22;
  if (b.kind === "property") {
    g.fillStyle = GROUP_COLOR[b.group!];
    g.fillRect(x + 1.5, y + 1.5, W - 3, BAR);
    g.strokeRect(x, y, W, BAR);
    g.fillStyle = "#161616";
    g.font = `bold 21px ${NARROW}`;
    wrap(g, b.name, cx, y + BAR + 14, W - 16, 24);
    g.font = `bold 22px ${NARROW}`;
    g.fillText(`${b.price} $`, cx, y + CR - 36);
    return;
  }
  g.font = `bold 21px ${NARROW}`;
  const n = wrap(g, b.name, cx, y + 14, W - 14, 24);
  if (b.kind === "opportunity") {
    g.fillStyle = "#d7261e";
    g.font = "bold 120px Georgia,serif";
    g.fillText("?", cx, y + 50 + n * 12);
  }
  else {
    const icon = { community_fund: "📦", railroad: "🚂", utility: i === 12 ? "💡" : "🚰", tax: i === 4 ? "💰" : "💍" }[b.kind as string] ?? "";
    g.font = '64px "Segoe UI Emoji",sans-serif';
    g.fillText(icon, cx, y + 30 + n * 26);
  }
  g.fillStyle = "#161616";
  g.font = `bold 20px ${NARROW}`;
  const value = b.price || b.tax;
  if (value)
    g.fillText((b.kind === "tax" ? "Pay " : "") + value + " $", cx, y + CR - 36);
}
function drawCorner(spec: RentBoard, g: CanvasRenderingContext2D, i: number) {
  const x = S - CR;
  const y = S - CR;
  const {kind,name}=spec.spaces[i];
  const words=name.toUpperCase().split(" ");
  const last=words.pop()!, first=words.join(" ");
  g.strokeStyle = "#1b1b1b";
  g.lineWidth = 3;
  g.strokeRect(x, y, CR, CR);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = "#161616";
  if (kind === "jail") {
    const C = CR * 0.66;
    const strip = CR - C;
    g.fillStyle = "#ee8a1e";
    g.fillRect(x, y, C, C);
    g.strokeRect(x, y, C, C);
    g.lineWidth = 6;
    for (let k = 1; k < 5; k++) {
      const bx = x + (k * C) / 5;
      g.beginPath();
      g.moveTo(bx, y + 8);
      g.lineTo(bx, y + C - 44);
      g.stroke();
    }
    g.fillStyle = "#fff";
    g.fillRect(x + 8, y + C - 40, C - 16, 32);
    g.fillStyle = "#161616";
    fitFont(g, "IN "+name.toUpperCase(), C - 26, 26, "Arial");
    g.fillText("IN "+name.toUpperCase(), x + C / 2, y + C - 23);
    fitFont(g, "JUST", C - 20, 30, "Arial");
    g.fillText("JUST", x + C / 2, y + C + strip / 2);
    g.save();
    g.translate(x + C + strip / 2, y + CR / 2);
    g.rotate(-Math.PI / 2);
    fitFont(g, "VISITING", CR - 24, 30, "Arial");
    g.fillText("VISITING", 0, 0);
    g.restore();
    return;
  }
  g.save();
  g.translate(x + CR / 2, y + CR / 2);
  g.rotate(-Math.PI / 4);
  if (kind === "start") {
    g.font = "bold 30px Arial";
    g.fillText(`COLLECT ${spec.start_salary} $`, 0, -62);
    g.fillStyle = "#c4161c";
    g.font = "bold 78px Georgia,serif";
    g.fillText(name.toUpperCase(), 0, 0);
    g.font = "bold 70px Arial";
    g.fillText("⟵", 0, 62);
  }
  else if (kind === "free_parking") {
    g.font = "bold 34px Arial";
    g.fillText(first, 0, -70);
    g.font = '90px "Segoe UI Emoji"';
    g.fillText("🚗", 0, 5);
    g.font = "bold 34px Arial";
    g.fillText(last, 0, 78);
  }
  else {
    g.font = "bold 32px Arial";
    g.fillText(first, 0, -72);
    g.font = '90px "Segoe UI Emoji"';
    g.fillText("👮", 0, 5);
    g.font = "bold 32px Arial";
    g.fillText(last, 0, 80);
  }
  g.restore();
}
export function boardTexture(spec: RentBoard, maxAniso: number): THREE.CanvasTexture {
  const [cv, g] = canvas(S, S);
  g.fillStyle = "#e9e2c9";
  g.fillRect(0, 0, S, S);
  g.fillStyle = "#d2e5cf";
  g.fillRect(CR, CR, S - 2 * CR, S - 2 * CR);
  const id = g.getImageData(0, 0, S, S);
  const d = id.data;
  for (let i = 0; i < d.length; i += 4) {
    const n = (Math.random() - 0.5) * 14;
    d[i] += n;
    d[i + 1] += n;
    d[i + 2] += n;
  }
  g.putImageData(id, 0, 0);
  for (let k = 0; k < 4; k++) {
    g.save();
    g.translate(S / 2, S / 2);
    g.rotate((k * Math.PI) / 2);
    g.translate(-S / 2, -S / 2);
    drawCorner(spec, g, k * 10);
    for (let j = 1; j <= 9; j++)
      drawTile(spec, g, k * 10 + j, S - CR - j * W, S - CR);
    g.restore();
  }
  g.strokeStyle = "#1b1b1b";
  g.lineWidth = 3;
  g.strokeRect(CR, CR, S - 2 * CR, S - 2 * CR);
  g.lineWidth = 6;
  g.strokeRect(3, 3, S - 6, S - 6);
  g.save();
  g.translate(S / 2, S / 2);
  g.rotate(-Math.PI / 4);
  g.fillStyle = "#c4161c";
  g.fillRect(-480, -100, 960, 200);
  g.strokeStyle = "#fff";
  g.lineWidth = 8;
  g.strokeRect(-462, -82, 924, 164);
  g.fillStyle = "#fff";
  g.textAlign = "center";
  g.textBaseline = "middle";
  fitFont(g, "RENTCRAFT", 820, 150, "Georgia,serif");
  g.fillText("RENTCRAFT", 0, 8);
  g.restore();
  for (const [x, y, col, r] of [
    [S / 2 - 330, S / 2 - 330, "#ee8a1e", (Math.PI * 3) / 4],
    [S / 2 + 330, S / 2 + 330, "#5fb3db", -Math.PI / 4],
  ] as [
    number,
    number,
    string,
    number
  ][]) {
    g.save();
    g.translate(x, y);
    g.rotate(r);
    g.setLineDash([18, 12]);
    g.strokeStyle = col;
    g.lineWidth = 6;
    g.strokeRect(-190, -120, 380, 240);
    g.restore();
  }
  return tex(cv, maxAniso);
}
export function woodTexture(): THREE.CanvasTexture {
  const [cv, g] = canvas(1024, 1024);
  g.fillStyle = "#5a321a";
  g.fillRect(0, 0, 1024, 1024);
  for (let y = 0; y < 1024; y += 2) {
    const v = Math.sin(y * 0.05 + Math.sin(y * 0.013) * 4) * 0.5 + 0.5;
    g.fillStyle = `rgba(${(30 + v * 40) | 0},${(15 + v * 20) | 0},${(5 + v * 8) | 0},${0.25 + Math.random() * 0.15})`;
    g.fillRect(0, y, 1024, 2);
  }
  for (let k = 0; k < 40; k++) {
    g.strokeStyle = `rgba(20,8,2,${Math.random() * 0.25})`;
    g.lineWidth = 1 + Math.random() * 2;
    g.beginPath();
    const y = Math.random() * 1024;
    g.moveTo(0, y);
    for (let x = 0; x <= 1024; x += 32)
      g.lineTo(x, y + Math.sin(x * 0.01 + k) * 6);
    g.stroke();
  }
  const t = tex(cv);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(3, 3);
  return t;
}
export function deckTexture(color: string, label: string, glyph: string): THREE.CanvasTexture {
  const [cv, g] = canvas(512, 320);
  g.fillStyle = color;
  g.fillRect(0, 0, 512, 320);
  g.strokeStyle = "rgba(255,255,255,.85)";
  g.lineWidth = 10;
  g.strokeRect(16, 16, 480, 288);
  g.fillStyle = "rgba(255,255,255,.22)";
  g.font = "bold 200px Georgia";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(glyph, 256, 170);
  g.fillStyle = "#fff";
  fitFont(g, label, 430, 64, "Georgia,serif");
  g.fillText(label, 256, 160);
  return tex(cv);
}
export const CW = 512;
export const CH = 660;
export function deedTexture(spec: RentBoard, space: number, mortgaged: boolean): THREE.CanvasTexture {
  const [cv, g] = canvas(CW, CH);
  const b = spec.spaces[space];
  g.fillStyle = "#fbf8ef";
  g.fillRect(0, 0, CW, CH);
  g.strokeStyle = "#bbb";
  g.lineWidth = 4;
  g.strokeRect(2, 2, CW - 4, CH - 4);
  g.textAlign = "center";
  g.textBaseline = "middle";
  const name = b.name.toUpperCase();
  if (mortgaged) {
    g.fillStyle = "#f0e9d6";
    g.fillRect(0, 0, CW, CH);
    g.fillStyle = "#b91c1c";
    g.font = "bold 60px Arial";
    g.fillText("MORTGAGED", CW / 2, 120);
    g.fillStyle = "#222";
    fitFont(g, name, 460, 60, NARROW);
    g.fillText(name, CW / 2, 230);
    g.font = "bold 40px Arial";
    g.fillText(`Price ${b.price} $`, CW / 2, 340);
    g.font = "38px Arial";
    g.fillText(`Mortgage ${b.mortgage_value} $`, CW / 2, 420);
    g.fillText(`Redeem ${b.mortgage_value + Math.ceil(b.mortgage_value / 10)} $`, CW / 2, 480);
  }
  else {
    const color = b.group ? GROUP_COLOR[b.group] : "#2b2b2b";
    g.fillStyle = color;
    g.fillRect(20, 20, CW - 40, 150);
    g.strokeStyle = "#222";
    g.lineWidth = 3;
    g.strokeRect(20, 20, CW - 40, 150);
    g.fillStyle = b.group && DARK_TEXT.has(b.group) ? "#111" : "#fff";
    g.font = "bold 26px Arial";
    g.fillText(b.kind === "property" ? "TITLE DEED" : b.kind === "railroad" ? "🚂 STATION" : "💡 UTILITY", CW / 2, 54);
    fitFont(g, name, 450, 64, NARROW);
    g.fillText(name, CW / 2, 118);
    g.fillStyle = "#161616";
    g.font = "bold 46px Arial";
    g.fillText(`PRICE ${b.price} $`, CW / 2, 218);
    g.font = `34px ${NARROW}`;
    const rows: [
      string,
      string
    ][] = b.kind === "property"
      ? [["Rent", b.rents![0]], ["Full group", b.rents![0] * 2], ["1 house", b.rents![1]], ["2 houses", b.rents![2]],
        ["3 houses", b.rents![3]], ["4 houses", b.rents![4]], ["Hotel", b.rents![5]]].map(([a, v]) => [String(a), `${v} $`])
      : b.kind === "railroad"
        ? spec.station_rents.map((rent, i) => [String(i + 1) + " stations", rent + " $"])
        : spec.utility_multipliers.map((factor, i) => [String(i + 1) + " utilities", factor + "× dice"]);
    rows.forEach(([a, v], k) => {
      g.textAlign = "left";
      g.fillText(a, 40, 280 + k * 45);
      g.textAlign = "right";
      g.fillText(v, CW - 40, 280 + k * 45);
    });
    g.textAlign = "center";
    g.font = "28px Arial";
    g.fillStyle = "#444";
    g.fillText(b.house_cost ? `House ${b.house_cost} $ · Mortgage ${b.mortgage_value} $` : `Mortgage ${b.mortgage_value} $`, CW / 2, CH - 36);
  }
  const t = tex(cv);
  return t;
}
export function deckName(spec:RentBoard,deck:string){
  return (spec.spaces.find(space=>space.kind===deck)?.name ?? deck.replaceAll("_"," ")).toUpperCase();
}
export function jailCardTexture(spec:RentBoard, deck: string): THREE.CanvasTexture {
  const [cv, g] = canvas(CW, CH);
  g.fillStyle = deck === "opportunity" ? "#ee8a1e" : "#4ea6d4";
  g.fillRect(0, 0, CW, CH);
  g.strokeStyle = "#fff";
  g.lineWidth = 10;
  g.strokeRect(18, 18, CW - 36, CH - 36);
  g.fillStyle = "#fff";
  g.textAlign = "center";
  const name=deckName(spec,deck);
  fitFont(g,name,CW-64,50,"Georgia");
  g.fillText(name,CW/2,110);
  const text=spec.cards[deck].find(card=>card.effect==="jail_free")?.text ?? "";
  const [title,...body]=text.split(". ");
  fitFont(g,title.toUpperCase(),CW-72,60,"Arial");
  g.fillText(title.toUpperCase(),CW/2,290);
  g.font="36px Arial";
  wrap(g,body.join(". "),CW/2,380,CW-84,48);
  return tex(cv);
}
export function pipTexture(n: number): THREE.CanvasTexture {
  const [cv, g] = canvas(256, 256);
  g.fillStyle = "#fbfaf5";
  g.fillRect(0, 0, 256, 256);
  g.fillStyle = "#151515";
  const P: Record<number, [
    number,
    number
  ][]> = {
    1: [[1, 1]], 2: [[0, 0], [2, 2]], 3: [[0, 0], [1, 1], [2, 2]], 4: [[0, 0], [2, 0], [0, 2], [2, 2]],
    5: [[0, 0], [2, 0], [1, 1], [0, 2], [2, 2]], 6: [[0, 0], [2, 0], [0, 1], [2, 1], [0, 2], [2, 2]],
  };
  for (const [a, b] of P[n]) {
    g.beginPath();
    g.arc(60 + a * 68, 60 + b * 68, 22, 0, 7);
    g.fill();
  }
  return tex(cv);
}

import './chart.css';
export interface Series {
    name: string;
    tone: string;
    points: [
        number,
        number
    ][];
    width?: number;
    tips?: string[];
    note?: string;
    dash?: string;
    ci?: number[];
    dx?: number;
}
export interface ChartOpts {
    yMin: number;
    yMax: number;
    width?: number;
    height?: number;
    baseline?: number;
    yFormat?: (v: number) => string;
    cursorX?: number | null;
    xMax?: number;
    fixed?: boolean;
}
export function lineChartSvg(series: Series[], o: ChartOpts): string {
    const W = o.width ?? 520;
    const H = o.height ?? 170;
    const pad = 30;
    const xs = series.flatMap((s) => s.points.map((p) => p[0]));
    const xMin = xs.length ? Math.min(...xs) : 0;
    const xMax = o.xMax ?? (xs.length ? Math.max(...xs) : 1);
    const fx = (x: number) => pad + ((x - xMin) / Math.max(1e-9, xMax - xMin)) * (W - pad - 6);
    const fy = (y: number) => H - 14 - ((Math.max(o.yMin, Math.min(o.yMax, y)) - o.yMin) / (o.yMax - o.yMin)) * (H - 24);
    const fmt = o.yFormat ?? ((v: number) => String(v));
    let svg = `<svg viewBox="0 0 ${W} ${H}" ${o.fixed ? `width="${W}" height="${H}"` : `width="100%"`} xmlns="http://www.w3.org/2000/svg">`;
    for (let k = 0; k <= 4; k++) {
        const v = o.yMin + ((o.yMax - o.yMin) * k) / 4;
        const y = fy(v);
        svg += `<line class="ch-grid" x1="${pad}" x2="${W - 6}" y1="${y}" y2="${y}"/><text class="ch-tick" x="${pad - 4}" y="${y + 3}" text-anchor="end">${fmt(v)}</text>`;
    }
    if (o.baseline != null) {
        const y = fy(o.baseline);
        svg += `<line class="ch-base" x1="${pad}" x2="${W - 6}" y1="${y}" y2="${y}" stroke-dasharray="4 4"/>`;
    }
    for (const s of series) {
        if (!s.points.length)
            continue;
        const pts = s.points.map(([x, y]) => `${(fx(x) + (s.dx ?? 0)).toFixed(1)},${fy(y).toFixed(1)}`).join(" ");
        svg += `<polyline class="ch-line tone-${s.tone}" stroke-width="${s.width ?? 2}" stroke-linejoin="round"${s.dash ? ` stroke-dasharray="${s.dash}"` : ""} points="${pts}"/>`;
    }
    for (const s of series) {
        if (!s.ci)
            continue;
        s.points.forEach(([x, y], i) => {
            const c = s.ci![i];
            if (!(c > 0))
                return;
            const cx = fx(x) + (s.dx ?? 0);
            const [y0, y1] = [fy(y + c), fy(y - c)];
            svg += `<path class="ch-err tone-${s.tone}" d="M${cx.toFixed(1)} ${y0.toFixed(1)}V${y1.toFixed(1)}M${(cx - 2.5).toFixed(1)} ${y0.toFixed(1)}h5M${(cx - 2.5).toFixed(1)} ${y1.toFixed(1)}h5"/>`;
        });
    }
    for (const s of series) {
        if (!s.tips)
            continue;
        s.points.forEach(([x, y], i) => {
            const cx = (fx(x) + (s.dx ?? 0)).toFixed(1);
            const cy = fy(y).toFixed(1);
            const tip = (s.tips![i] ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
            svg += `<g class="pt" data-tip="${tip}"><circle class="ch-hit" cx="${cx}" cy="${cy}" r="9"/><circle class="ch-dot tone-${s.tone}" cx="${cx}" cy="${cy}" r="3" stroke-width="1"/></g>`;
        });
    }
    if (o.cursorX != null) {
        const x = fx(o.cursorX);
        svg += `<line class="ch-cursor" x1="${x}" x2="${x}" y1="6" y2="${H - 14}" stroke-width="1.5"/>`;
    }
    return svg + "</svg>";
}
export interface ScrollChartOpts {
    /** Fit all points by default; false explicitly enables minimum-spacing scrolling. */
    fit?: boolean;
    xMin?: number;
    xMax?: number;
    /** Total available width, including the pinned Y axis, when measured by the host. */
    viewportWidth?: number;
    ariaLabel?: string;
    axisWidth?: number;
    yTicks?: number[];
    xTicks?: number[];
    cursorPoints?: [number, number][];
    /** Separate sample precision from compact axis tick labels. */
    valueFormat?: (v:number)=>string;
    key: string;
    group?: string;
    yMin: number;
    yMax: number;
    height?: number;
    minWidth?: number;
    pxPerPoint?: number;
    yFormat?: (v: number) => string;
    xFormat?: (x: number) => string;
    smooth?: number;
}
export function movingAverage(points: [
    number,
    number
][], window: number): [
    number,
    number
][] {
    let sum = 0;
    return points.map(([x, y], i) => {
        sum += y;
        if (i >= window)
            sum -= points[i - window][1];
        return [x, sum / Math.min(i + 1, window)];
    });
}
const AXIS = 36;
/** Rounded ticks may extend the domain; integer ticks never use fractional steps. */
export function niceTicks(min:number,max:number,count=5,integer=false):number[] {
    if(!Number.isFinite(min)||!Number.isFinite(max))return [];
    if(min>max)[min,max]=[max,min];
    if(min===max){const padding=Math.abs(min)*0.1||1;min-=padding;max+=padding;}
    const rough=(max-min)/Math.max(1,count-1),power=10**Math.floor(Math.log10(rough));
    let step=([1,2,2.5,5,10].find(v=>v*power>=rough)??10)*power;
    if(integer)step=Math.max(1,Math.ceil(step));
    const first=Math.floor(min/step),last=Math.ceil(max/step);
    return Array.from({length:Math.min(100,Math.max(0,last-first+1))},(_,i)=>Number(((first+i)*step).toPrecision(12)));
}
const groupState = new Map<string, {
    atEnd: boolean;
    center: number;
}>();
const hoverStep = new Map<string, number>();
const chartData = new Map<string, {
    pts: [
        number,
        number
    ][];
    avg: [
        number,
        number
    ][] | null;
    fmt: (v: number) => string;
    fy: (y: number) => number;
}>();
export function scrollChartHtml(series: Series[], o: ScrollChartOpts): string {
    const axisWidth=o.axisWidth??AXIS;
    const H = o.height ?? 100;
    const xs = series.flatMap((s) => s.points.map((p) => p[0]));
    const n = new Set(xs).size;
    const minimum = o.fit===false?Math.max(0,n-1)*(o.pxPerPoint??3)+12:12;
    const measured=o.viewportWidth!==undefined&&o.viewportWidth>0;
    const W=measured?Math.max(1,o.viewportWidth!-axisWidth,minimum):Math.max(1,(o.minWidth??300)-axisWidth);
    const xMin = o.xMin??(xs.length ? Math.min(...xs) : 0);
    const xMax = o.xMax??(xs.length ? Math.max(...xs) : 1);
    const fx = (x: number) => 4 + ((x - xMin) / Math.max(1e-9, xMax - xMin)) * (W - 12);
    const fy = (y: number) => H - 16 - ((Math.max(o.yMin, Math.min(o.yMax, y)) - o.yMin) / (o.yMax - o.yMin || 1)) * (H - 24);
    const fmt = o.yFormat ?? ((v: number) => String(v));
    const xfmt = o.xFormat ?? ((x: number) => `${(x / 1e6).toFixed(2).replace(".", ",")} mln`);
    const label=(o.ariaLabel??'Chart').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
    let axis = `<svg width="${axisWidth}" height="${H}" style="width:${axisWidth}px" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">`;
    let plot = `<svg width="${measured?W:'100%'}" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:${measured?W+'px':'100%'};min-width:${minimum}px" role="img" aria-label="${label}" xmlns="http://www.w3.org/2000/svg">`;
    for (const [index,v] of (o.yTicks??Array.from({length:5},(_,k)=>o.yMin+(o.yMax-o.yMin)*k/4)).entries()) {
        const y = fy(v);
        axis += `<text data-chart-node="y-label:${index}" class="ch-tick" x="${axisWidth - 8}" y="${y + 3}" text-anchor="end">${fmt(v)}</text>`;
        plot += `<line data-chart-node="y-grid:${index}" class="ch-grid" x1="0" x2="${W}" y1="${y}" y2="${y}"/>`;
    }
    if (xs.length > 1) {
        const ticks=o.xTicks??Array.from({length:Math.max(0,Math.ceil((W-90)/140))},(_,i)=>xMin+((60+i*140-4)/(W-12))*(xMax-xMin));
        for (const [index,x] of ticks.filter(x=>x>=xMin&&x<=xMax).entries()) {
            const px=fx(x);
            plot += `<line data-chart-node="x-grid:${index}" class="ch-grid" x1="${px}" x2="${px}" y1="6" y2="${H - 16}" stroke-opacity=".6"/><text data-chart-node="x-label:${index}" class="ch-tick" x="${px}" y="${H - 4}" text-anchor="middle">${xfmt(x)}</text>`;
        }
    }
    const line = (pts: [
        number,
        number
    ][], tone: string, width: number, key:string, extra = "") => `<polyline data-chart-node="${key}" class="ch-line tone-${tone}" stroke-width="${width}" stroke-linejoin="round"${extra} points="${pts.map(([x, y]) => `${fx(x).toFixed(1)},${fy(y).toFixed(1)}`).join(" ")}"/>`;
    for (const [index,s] of series.entries()) {
        if (!s.points.length)
            continue;
        if(s.points.length===1){const [x,y]=s.points[0];plot+=`<circle data-chart-node="series:${index}:raw" class="ch-dot tone-${s.tone}" cx="${fx(x).toFixed(1)}" cy="${fy(y).toFixed(1)}" r="3"/>`;continue;}
        if (o.smooth && s.points.length > 2) {
            plot += line(s.points, s.tone, 1, `series:${index}:raw`, ` stroke-opacity="0.35"`);
            plot += line(movingAverage(s.points, o.smooth), s.tone, 2.5, `series:${index}:trend`);
        }
        else
            plot += line(s.points, s.tone, s.width ?? 1.5, `series:${index}:raw`, s.dash ? ` stroke-dasharray="${s.dash}"` : "");
    }
    const first = series.find((x) => x.points.length);
    if (first) {
        const label = o.valueFormat ?? o.yFormat ?? ((v: number) => v.toFixed(2).replace(".", ","));
        const points=o.cursorPoints??first.points;
        chartData.set(o.key, { pts: points, avg: o.smooth && points.length > 2 ? movingAverage(points, o.smooth) : null, fmt: label, fy });
    }
    plot += `<line data-chart-node="cursor-line" class="xhair" x1="0" x2="0" y1="4" y2="${H - 16}" stroke-width="1" visibility="hidden"/>`;
    plot += `<circle data-chart-node="cursor-dot" class="xhair-d" cx="0" cy="0" r="3" visibility="hidden"/>`;
    plot += `<text data-chart-node="cursor-label" class="xhair-t" x="0" y="11" visibility="hidden"></text>`;
    const attrs = `data-key="${o.key}" data-group="${o.group ?? o.key}" data-x0="${xMin}" data-x1="${xMax}" data-w="${W}"`;
    // Reserve scrollbar space before the data becomes dense enough to overflow.
    return `<div class="cscroll" style="height:${H+16}px"><div class="cscroll-axis">${axis}</svg></div><div class="cscroll-plot" ${attrs}>${plot}</svg></div></div>`;
}
function patchChartElement(current:Element,next:Element):void {
    for(const attr of [...current.attributes])if(!next.hasAttribute(attr.name))current.removeAttribute(attr.name);
    for(const attr of [...next.attributes])if(current.getAttribute(attr.name)!==attr.value)current.setAttribute(attr.name,attr.value);
    if(!next.children.length){if(current.textContent!==next.textContent)current.textContent=next.textContent;return;}
    const key=(node:Element,index:number)=>node.getAttribute('data-chart-node')??`${node.localName}:${index}`;
    const existing=new Map([...current.children].map((node,index)=>[key(node,index),node]));
    const used=new Set<Element>();
    [...next.children].forEach((child,index)=>{
        let node=existing.get(key(child,index));
        if(!node||node.localName!==child.localName)node=child.cloneNode(true) as Element;
        else patchChartElement(node,child);
        used.add(node);if(current.children[index]!==node)current.insertBefore(node,current.children[index]??null);
    });
    for(const child of [...current.children])if(!used.has(child))child.remove();
}
/** One renderer serves initial mount, data updates, reveal and container resizing. */
export function mountScrollChart(root:HTMLElement,initialSeries:Series[],initialOptions:ScrollChartOpts,scope:HTMLElement=root){
    let series=initialSeries,options=initialOptions,disposed=false,lastWidth:number|undefined;
    const measuredWidth=()=>{const width=root.getBoundingClientRect().width||root.clientWidth;return width>0?width:undefined;};
    function render(){
        if(disposed)return;
        lastWidth=measuredWidth();
        const template=document.createElement('template');template.innerHTML=scrollChartHtml(series,{...options,viewportWidth:lastWidth??options.viewportWidth});
        if(!root.querySelector('.cscroll'))root.replaceChildren(template.content);
        else patchChartElement(root.querySelector('.cscroll')!,template.content.querySelector('.cscroll')!);
        keepScroll(scope);
    }
    bindChartSync(scope);bindChartTips(scope);render();
    const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(()=>{if(!disposed&&measuredWidth()!==lastWidth)render();});observer?.observe(root);
    return {update(nextSeries:Series[],nextOptions:ScrollChartOpts){if(disposed)return;if(options.key!==nextOptions.key)chartData.delete(options.key);series=nextSeries;options=nextOptions;render();},dispose(){if(disposed)return;disposed=true;observer?.disconnect();chartData.delete(options.key);root.replaceChildren();}};
}
const geom = (el: HTMLElement) => ({ x0: Number(el.dataset.x0), x1: Number(el.dataset.x1), w: Number(el.dataset.w) });
const plotScale=(el:HTMLElement)=>{const width=el.querySelector('svg')?.getBoundingClientRect().width;return width?width/geom(el).w:1;};
const pxOf = (el: HTMLElement, x: number) => {
    const g = geom(el);
    return 4 + ((x - g.x0) / Math.max(1e-9, g.x1 - g.x0)) * (g.w - 12);
};
const stepAt = (el: HTMLElement, px: number) => {
    const g = geom(el);
    return g.x0 + ((px - 4) / Math.max(1e-9, g.w - 12)) * (g.x1 - g.x0);
};
const groupPlots = (root: ParentNode, group: string) => [...root.querySelectorAll<HTMLElement>(".cscroll-plot")].filter((el) => el.dataset.group === group);
function nearest(pts: [
    number,
    number
][], x: number): number {
    let lo = 0;
    let hi = pts.length - 1;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (pts[mid][0] < x)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo > 0 && Math.abs(pts[lo - 1][0] - x) <= Math.abs(pts[lo][0] - x) ? lo - 1 : lo;
}
function drawHair(el: HTMLElement, step: number | undefined): void {
    const line = el.querySelector<SVGLineElement>(".xhair");
    const text = el.querySelector<SVGTextElement>(".xhair-t");
    const dot = el.querySelector<SVGCircleElement>(".xhair-d");
    if (!line || !text || !dot)
        return;
    const g = geom(el);
    const data = chartData.get(el.dataset.key!);
    const show = step != null && step >= g.x0 && step <= g.x1 && !!data;
    for (const node of [line, text, dot])
        node.setAttribute("visibility", show ? "visible" : "hidden");
    if (!show)
        return;
    const i = nearest(data!.pts, step!);
    const [px, value] = data!.pts[i];
    const x = pxOf(el, px);
    line.setAttribute("x1", x.toFixed(1));
    line.setAttribute("x2", x.toFixed(1));
    const trend = data!.avg ? data!.avg[i][1] : null;
    dot.setAttribute("cx", x.toFixed(1));
    dot.setAttribute("cy", data!.fy(trend ?? value).toFixed(1));
    text.textContent = data!.fmt(value) + (trend != null ? ` · trend ${data!.fmt(trend)}` : "");
    const right = x + 4 + text.textContent.length * 5 < g.w;
    text.setAttribute("x", (right ? x + 4 : x - 4).toFixed(1));
    text.setAttribute("text-anchor", right ? "start" : "end");
}
function applyScroll(el: HTMLElement, st: {
    atEnd: boolean;
    center: number;
} | undefined): void {
    const want = !st || st.atEnd ? el.scrollWidth - el.clientWidth : pxOf(el, st.center)*plotScale(el) - el.clientWidth / 2;
    if (Math.abs(el.scrollLeft - want) > 2)
        el.scrollLeft = want;
}
const syncBound=new WeakSet<HTMLElement>(),tipsBound=new WeakSet<HTMLElement>(),scrollBound=new WeakSet<HTMLElement>();
export function bindChartSync(root: HTMLElement): void {
    if(syncBound.has(root))return;syncBound.add(root);
    const move = (ev: MouseEvent) => {
        const plot = (ev.target as Element).closest?.<HTMLElement>(".cscroll-plot");
        const prev = [...hoverStep.keys()];
        if (!plot || !root.contains(plot)) {
            prev.forEach((g) => groupPlots(root, g).forEach((el) => drawHair(el, undefined)));
            hoverStep.clear();
            return;
        }
        const svg = plot.querySelector("svg")!;
        const step = stepAt(plot, (ev.clientX - svg.getBoundingClientRect().left)/plotScale(plot));
        const group = plot.dataset.group!;
        hoverStep.clear();
        hoverStep.set(group, step);
        prev.filter((g) => g !== group).forEach((g) => groupPlots(root, g).forEach((el) => drawHair(el, undefined)));
        groupPlots(root, group).forEach((el) => drawHair(el, step));
    };
    root.addEventListener("pointermove", move);
    root.addEventListener("pointerdown", move);
    root.addEventListener("pointerleave", () => {
        for (const g of hoverStep.keys())
            groupPlots(root, g).forEach((el) => drawHair(el, undefined));
        hoverStep.clear();
    });
}
export function keepScroll(root: ParentNode): void {
    root.querySelectorAll<HTMLElement>(".cscroll-plot[data-group]").forEach((el) => {
        const group = el.dataset.group!;
        applyScroll(el, groupState.get(group));
        drawHair(el, hoverStep.get(group));
        if(scrollBound.has(el))return;scrollBound.add(el);
        el.addEventListener("scroll", () => {
            const st = {
                atEnd: el.scrollLeft + el.clientWidth >= el.scrollWidth - 4,
                center: stepAt(el, (el.scrollLeft + el.clientWidth / 2)/plotScale(el)),
            };
            groupState.set(group, st);
            groupPlots(root, group).forEach((o) => o !== el && applyScroll(o, st));
        });
    });
}
export function chartGrid(total: number, n: number, min = 260, gap = 8): {
    cols: number;
    width: number;
} {
    const cols = Math.max(1, Math.min(n, Math.floor((total + gap) / (min + gap))));
    return { cols, width: (total - gap * (cols - 1)) / cols };
}
export function legendHtml(series: Series[]): string {
    return `<div class="legend">${series.map((s) => `<span><i class="tone-${s.tone}"></i>${s.name}${s.note ? ` ${s.note}` : ""}</span>`).join("")}</div>`;
}
export function bindChartTips(root: HTMLElement): void {
    if(tipsBound.has(root))return;tipsBound.add(root);
    let tip = document.querySelector<HTMLDivElement>("#chart-tip");
    if (!tip) {
        tip = document.createElement("div");
        tip.id = "chart-tip";
        tip.className = "chart-tip hide";
        document.body.appendChild(tip);
    }
    const box = tip;
    const show = (ev: MouseEvent | PointerEvent) => {
        const g = (ev.target as Element).closest?.("[data-tip]");
        if (!g || !root.contains(g))
            return void box.classList.add("hide");
        box.textContent = g.getAttribute("data-tip") ?? "";
        box.classList.remove("hide");
        const x = Math.min(ev.clientX + 12, window.innerWidth - box.offsetWidth - 8);
        const y = ev.clientY + 14 + box.offsetHeight > window.innerHeight ? ev.clientY - box.offsetHeight - 10 : ev.clientY + 14;
        box.style.left = `${Math.max(8, x)}px`;
        box.style.top = `${y}px`;
    };
    root.addEventListener("pointermove", show);
    root.addEventListener("pointerdown", show);
    root.addEventListener("pointerleave", () => box.classList.add("hide"));
}

export const PAGE_SIZE = 12;
export interface Page<T> {
    items: T[];
    page: number;
    pages: number;
    total: number;
}
export function pageOf<T>(all: T[], page: number, size = PAGE_SIZE): Page<T> {
    const pages = Math.max(1, Math.ceil(all.length / size));
    const p = Math.min(Math.max(0, page), pages - 1);
    return { items: all.slice(p * size, (p + 1) * size), page: p, pages, total: all.length };
}
export async function fetchPage<T>(url: string, page: number, size = PAGE_SIZE): Promise<Page<T>> {
    const get = async (p: number) => {
        const r = await fetch(`${url}${url.includes("?") ? "&" : "?"}offset=${p * size}&limit=${size}`);
        if (!r.ok)
            throw new Error(`Server error (${r.status})`);
        const items = (await r.json()) as T[];
        return { items, total: Number(r.headers.get("X-Total-Count") ?? items.length) };
    };
    let p = Math.max(0, page);
    let { items, total } = await get(p);
    const pages = Math.max(1, Math.ceil(total / size));
    if (p > pages - 1) {
        p = pages - 1;
        ({ items, total } = await get(p));
    }
    return { items, page: p, pages, total };
}
export interface PagerLabels { newer:string; older:string; page(current:number,total:number):string }
export function pagerHtml(p: Page<unknown>, noun: string, size = PAGE_SIZE, labels:PagerLabels={newer:'← Newer',older:'Older →',page:(current,total)=>`page ${current} of ${total}`}): string {
    if (p.total <= size)
        return "";
    const t = {...labels,of:labels.page(p.page+1,p.pages)};
    return `<button class="btn sm" data-page="-1" ${p.page === 0 ? "disabled" : ""}>${t.newer}</button>
    <span class="muted">${t.of} · ${p.total} ${noun}</span>
    <button class="btn sm" data-page="1" ${p.page >= p.pages - 1 ? "disabled" : ""}>${t.older}</button>`;
}
let revealNext = false;
export function requestReveal(): void {
    revealNext = true;
}
export function revealRequested(el: Element | null): void {
    if (!revealNext)
        return;
    revealNext = false;
    if (el && el.getBoundingClientRect().top > window.innerHeight * 0.6)
        el.scrollIntoView?.({ block: "start" });
}
export function bindPager(el: Element, move: (delta: number) => void): void {
    el.addEventListener("click", (e) => {
        const d = (e.target as HTMLElement).closest<HTMLElement>("[data-page]")?.dataset.page;
        if (d)
            move(Number(d));
    });
}

/** Navigation accepts feature entries from its host; it never imports them. */
import { PALETTES, applyPalette, currentPalette, type PaletteId } from "./palette";
export interface NavigationItem { id: string; label: string; title?: string }
export interface NavigationOptions { brand: string; emblem: string; active: string; items: NavigationItem[]; select(id:string):void;
  /** The compact colour menu at the right of the bar (default on), as in the shared design. */
  palette?: boolean;
  /** The "📖 Rules" button at the right end of the bar (rules of all games), as in the shared design. */
  rules?: () => void }

/** The 🎨 colour menu: a button with a small popover of the palettes; the choice is saved in this browser. */
export function paletteMenu(): HTMLElement {
  const wrap=document.createElement('div');wrap.className='palette-menu';
  const open=document.createElement('button');open.type='button';open.className='palette-btn';open.textContent='🎨';
  open.title='Colours';open.setAttribute('aria-label','Colours');open.setAttribute('aria-expanded','false');open.setAttribute('aria-haspopup','true');
  const pop=document.createElement('div');pop.className='palette-pop';pop.hidden=true;pop.setAttribute('role','group');pop.setAttribute('aria-label','Colours');
  const title=document.createElement('b');title.textContent='Colours:';
  const seg=document.createElement('span');seg.className='seg';
  const note=document.createElement('span');note.className='muted';note.textContent='saved in this browser';
  const mark=(id:PaletteId)=>seg.querySelectorAll<HTMLButtonElement>('button').forEach(b=>{const on=b.dataset.palette===id;b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on));});
  for(const p of PALETTES){const b=document.createElement('button');b.type='button';b.dataset.palette=p.id;b.textContent=p.name;
    b.onclick=()=>{try{localStorage.setItem('boardcraft-palette',p.id);}catch{/* applied for this visit */}applyPalette(p.id);mark(p.id);};seg.append(b);}
  mark(currentPalette());
  const box=document.createElement('div');box.className='palette-switch';box.append(title,seg,note);pop.append(box);
  const toggle=(show:boolean)=>{pop.hidden=!show;open.setAttribute('aria-expanded',String(show));if(show)mark(currentPalette());};
  open.onclick=()=>toggle(pop.hidden);
  pop.addEventListener('keydown',e=>{if(e.key==='Escape'){toggle(false);open.focus();}});
  const outside=(e:Event)=>{if(!wrap.isConnected){document.removeEventListener('pointerdown',outside,true);document.removeEventListener('click',outside,true);return;}if(!pop.hidden&&!wrap.contains(e.target as Node))toggle(false);};
  document.addEventListener('pointerdown',outside,true);
  document.addEventListener('click',outside,true);
  wrap.append(open,pop);
  return wrap;
}
export function mountNavigation(root: HTMLElement, options: NavigationOptions): void {
    root.className='top';
    const brand=document.createElement('a');brand.className='brand';brand.href='#/play';
    const plate=document.createElement('span');plate.className='plate';
    const image=document.createElement('img');image.src=options.emblem;image.alt='';plate.append(image);
    const word=document.createElement('span');word.className='word';word.textContent=options.brand;brand.append(plate,word);
    brand.onclick=e=>{e.preventDefault();options.select('play');};
    const nav=document.createElement('nav');nav.setAttribute('aria-label','Navigation');
    for(const item of options.items){const button=document.createElement('button');button.type='button';button.dataset.page=item.id;button.textContent=item.label;if(item.title)button.title=item.title;if(item.id===options.active){button.className='on';button.setAttribute('aria-current','page');}button.onclick=()=>options.select(item.id);nav.append(button);}
    root.replaceChildren(brand,nav);
    if(options.palette!==false)root.append(paletteMenu());
    if(options.rules){const rules=document.createElement('button');rules.type='button';rules.className='btn rules-btn';rules.textContent='📖 Rules';rules.onclick=()=>options.rules!();root.append(rules);}
}

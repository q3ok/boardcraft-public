import { expect, test, vi } from 'vitest';
import { pageOf, pagerHtml } from '../src/ui/pager';
import { bindChartSync, lineChartSvg, scrollChartHtml } from '../src/ui/chart';
import { mountNavigation } from '../src/ui/nav';
test('shared pagination clamps to existing pages and chart preserves signed points',()=>{
 expect(pageOf([1,2,3],90,2)).toEqual({items:[3],page:1,pages:2,total:3});
 expect(pagerHtml(pageOf([1,2,3],0,2),'items',2)).toContain('disabled');
 expect(lineChartSvg([{name:'Loss',tone:'red',points:[[1,-2],[2,1]]}],{yMin:-3,yMax:2})).toContain('polyline');
});
test('cursor follows later segments of a primary curve',()=>{
 const root=document.createElement('div');root.innerHTML=scrollChartHtml([{name:'A',tone:'gold',points:[[0,1]]},{name:'A',tone:'gold',points:[[2,9]]}],{key:'segments',yMin:0,yMax:10,cursorPoints:[[0,1],[2,9]]});
 bindChartSync(root);const plot=root.querySelector<HTMLElement>('.cscroll-plot')!;
 plot.dispatchEvent(new MouseEvent('pointermove',{bubbles:true,clientX:Number(plot.dataset.w)-8}));
 expect(root.querySelector('.xhair-t')!.textContent).toContain('9');
});
test('navigation renders caller-owned extensions without importing feature modules',()=>{
 const root=document.createElement('header'),selected=vi.fn();
 mountNavigation(root,{brand:'Boardcraft',emblem:'/emblem.svg',active:'extra',items:[{id:'play',label:'Play'},{id:'extra',label:'Extra'}],select:selected});
 root.querySelector<HTMLButtonElement>('[data-page="extra"]')!.click();
 expect(selected).toHaveBeenCalledWith('extra');expect(root.querySelector('[aria-current="page"]')!.textContent).toBe('Extra');
});

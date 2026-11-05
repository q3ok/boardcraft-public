import {afterEach,expect,test,vi} from 'vitest';
import {mountScrollChart,scrollChartHtml,type Series} from '../src/ui/chart';
const data=(count:number):Series[]=>[{name:'Value',tone:'gold',points:Array.from({length:count},(_,i)=>[i,i%10])}];
const options={key:'responsive',axisWidth:80,yMin:0,yMax:10,height:150,pxPerPoint:3,fit:false};
afterEach(()=>vi.unstubAllGlobals());

test('fit mode keeps the entire domain in the viewport and preserves exact cursor data',()=>{
 const root=document.createElement('div');root.innerHTML=scrollChartHtml(data(512),{...options,fit:true,viewportWidth:800});
 const svg=root.querySelector<SVGElement>('.cscroll-plot svg')!,plot=root.querySelector<HTMLElement>('.cscroll-plot')!;
 expect(svg.getAttribute('width')).toBe('720');expect(svg.style.minWidth).toBe('12px');expect(plot.dataset.x1).toBe('511');
 expect(svg.querySelector('polyline')!.getAttribute('points')!.split(' ')).toHaveLength(512);
 root.innerHTML=scrollChartHtml(data(1),{...options,fit:true});expect(root.querySelector('circle.ch-dot')).not.toBeNull();
});

test('fitted cursor reads the exact original sample and explicit scrolling preserves the SVG',()=>{
 const root=document.createElement('div');document.body.append(root);vi.spyOn(root,'getBoundingClientRect').mockReturnValue({width:800} as DOMRect);
 const series:Series[]=[{name:'Value',tone:'gold',points:Array.from({length:512},(_,i)=>[i,i+0.123456789])}];
 const chart=mountScrollChart(root,series,{...options,fit:true,yMax:512,valueFormat:v=>String(v)}),svg=root.querySelector('.cscroll-plot svg')!;
 svg.dispatchEvent(new MouseEvent('pointermove',{bubbles:true,clientX:4+477/511*708}));
 expect(svg.querySelector('.xhair-t')!.textContent).toBe('477.123456789');
 chart.update(series,{...options,fit:false,yMax:512});expect(root.querySelector('.cscroll-plot svg')).toBe(svg);expect(Number(svg.getAttribute('width'))).toBeGreaterThan(720);chart.dispose();root.remove();
});
test('one viewport gives ten and 120 points the same SVG width; only dense data scroll',()=>{
 const root=document.createElement('div');
 const width=(count:number)=>{root.innerHTML=scrollChartHtml(data(count),{...options,viewportWidth:800});return Number(root.querySelector('.cscroll-plot svg')!.getAttribute('width'));};
 expect(width(10)).toBe(720);expect(width(120)).toBe(720);expect(width(400)).toBeGreaterThan(720);
 root.innerHTML=scrollChartHtml(data(10),options);const svg=root.querySelector<SVGElement>('.cscroll-plot svg')!;
 expect(svg.style.width).toBe('100%');expect(svg.getAttribute('viewBox')).toBeTruthy();
});
test('updates and resize retain the chart, plot SVG and curve nodes',()=>{
 let resize!:ResizeObserverCallback;const disconnect=vi.fn();vi.stubGlobal('ResizeObserver',class{constructor(cb:ResizeObserverCallback){resize=cb;}observe(){}disconnect=disconnect;});
 const root=document.createElement('div');document.body.append(root);let width=800;
 vi.spyOn(root,'getBoundingClientRect').mockImplementation(()=>({width,height:150,x:0,y:0,top:0,left:0,bottom:150,right:width,toJSON(){}}));
 const chart=mountScrollChart(root,data(10),options),container=root.querySelector('.cscroll'),plot=root.querySelector<HTMLElement>('.cscroll-plot')!,svg=plot.querySelector('svg')!,curve=svg.querySelector('polyline')!;
 const listeners=vi.spyOn(plot,'addEventListener'),originalPoints=curve.getAttribute('points');
 chart.update(data(120),options);
 expect(root.querySelector('.cscroll')).toBe(container);expect(root.querySelector('.cscroll-plot')).toBe(plot);expect(plot.querySelector('svg')).toBe(svg);expect(svg.querySelector('polyline')).toBe(curve);
 expect(curve.getAttribute('points')).not.toBe(originalPoints);expect(curve.getAttribute('points')!.split(' ')).toHaveLength(120);
 expect(svg.getAttribute('width')).toBe('720');expect((container as HTMLElement).style.height).toBe('166px');expect(listeners.mock.calls.filter(([name])=>name==='scroll')).toHaveLength(0);
 width=600;resize([],{} as ResizeObserver);expect(svg.getAttribute('width')).toBe('520');expect(plot.querySelector('svg')).toBe(svg);
 chart.update(data(400),options);expect(Number(svg.getAttribute('width'))).toBeGreaterThan(520);expect((container as HTMLElement).style.height).toBe('166px');
 chart.dispose();expect(disconnect).toHaveBeenCalledOnce();resize([],{} as ResizeObserver);expect(root.childElementCount).toBe(0);root.remove();
});

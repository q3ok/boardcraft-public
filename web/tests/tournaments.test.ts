import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { mountTournaments } from "../src/tournaments";

const viewPort = vi.hoisted(()=>({mount:vi.fn(),update:vi.fn(),dispose:vi.fn()}));
vi.mock("../src/game-view",()=>({mountGameView:(root:HTMLElement,options:unknown)=>{viewPort.mount(root,options);return {update:viewPort.update,dispose:viewPort.dispose};}}));
const field = (type:string) => ({type,description:`Explicit ${type} fixture value.`});
const tournament = {type:"object",required:["name","config","seats","seeds"],properties:{
  name:field("string"),config:{$ref:"#/$defs/Config"},rules_version:field("string"),engine_version:field("string"),
  seats:{...field("array"),minItems:1}, seeds:{...field("array"),minItems:1},sampler_seeds:field("array"),rotations:field("array"),
  match_count:field("integer"),schedule_cap:field("integer"),max_inflight:field("integer"),max_record_bytes:field("integer"),max_decisions:field("integer")},
  $defs:{Config:{type:"object",required:["game_id","variant","players"],properties:{game_id:field("string"),variant:field("string"),players:field("integer")}}}};
const fit = {type:"object",required:["gauge","elo_origin"],properties:{gauge:{const:"component_sum_zero",description:"Component gauge"},elo_origin:field("number"),ridge_lambda:field("number"),prior_pseudocount:field("number"),solver:{const:"newton_backtracking"},max_iterations:field("integer"),max_backtracks:field("integer"),gradient_inf_tolerance:field("number"),armijo_coefficient:field("number"),backtrack_factor:field("number")}};
const games=[{game_id:"fixture",name:"Fixture game",variants:["standard"],min_players:1,max_players:5,rules_version:"r1",engine_version:"e1"}];
const saved=(id="t1")=>({tournament_id:id,spec_sha:"a".repeat(64),revision:7,status:"pending",cleanup_unconfirmed:false,spec:{name:id,config:{game_id:"fixture",variant:"standard",players:1},references:[{kind:"builtin",identity:"policy"}]},matches:[]});
const cleanup:Array<()=>void>=[];
beforeEach(()=>{viewPort.mount.mockClear();viewPort.update.mockClear();viewPort.dispose.mockClear(); for(const name of ["localStorage","sessionStorage"]){const entries=new Map<string,string>();vi.stubGlobal(name,{getItem:(k:string)=>entries.get(k)??null,setItem:(k:string,v:string)=>entries.set(k,v),clear:()=>entries.clear(),get length(){return entries.size;}});}});
afterEach(()=>{cleanup.splice(0).forEach(fn=>fn());vi.restoreAllMocks();window.sessionStorage.clear();window.localStorage.clear();});
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
function setup(extra?:(path:string,body?:any,credential?:string)=>any){
  const calls:Array<{path:string,body:any,credential?:string}>=[];
  const root=document.createElement("div");document.body.append(root);
  const ui=mountTournaments(root,{request:async(path,body,credential)=>{
    calls.push({path,body,credential});
    const answer=extra?.(path,body,credential);if(answer!==undefined)return answer;
    if(path.endsWith("/schema"))return {tournament,fit};
    if(path.endsWith("/settings"))return {table:{properties:{}},live:{properties:{}},simulation_defaults:{},bots:{}};
    if(path==="/api/games")return games;
    if(path.endsWith("/bots"))return [{choice_id:"fixture-bot",display_name:"Fixture bot",kind:"builtin",reference:{}}];
    if(path==="/api/tournaments")return [];
    return saved(path.split("/").pop());
  }});cleanup.push(()=>{ui.dispose();root.remove();});return {root,calls,ui};
}
function control(root:HTMLElement,path:string){return root.querySelector<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>(`[data-path="${path}"]`)!;}
function value(root:HTMLElement,path:string,text:string){const input=control(root,path);expect(input,path).not.toBeNull();input.value=text;input.dispatchEvent(new Event("input"));input.dispatchEvent(new Event("change"));}
function click(root:HTMLElement,text:string){const b=Array.from(root.querySelectorAll("button")).find(b=>b.textContent===text)!;expect(b,text).toBeTruthy();b.click();}

test("discovery choices remain blank and incomplete forms never create or fit",async()=>{
  const {root,calls}=setup();await settle();
  expect(control(root,"config.game_id").tagName).toBe("SELECT");
  expect(control(root,"config.game_id").value).toBe("");
  expect(root.textContent).toContain("Fixture game");
  root.querySelector("form")!.dispatchEvent(new Event("submit",{cancelable:true}));await settle();
  expect(calls.filter(c=>c.body!==undefined)).toEqual([]);
  value(root,"config.game_id","fixture");await settle();
  expect(control(root,"config.variant").value).toBe("");
  expect(control(root,"config.players").value).toBe("");
  value(root,"config.players","1");
  expect(root.querySelector('[data-seat="0"]')!.textContent).toContain("Fixture bot");
  expect((root.querySelector('[data-seat="0"]') as HTMLSelectElement).value).toBe("");
  expect(calls.some(c=>/start|restart|fit$/.test(c.path))).toBe(false);
});

test("all schema fields have help and JSON import updates controls and submitted values",async()=>{
  const {root,calls}=setup((path,body)=>path==="/api/tournaments"&&body?{...saved(),creator_credential:"private-capability"}:undefined);await settle();
  const fields=["name","config.game_id","config.variant","config.players","rules_version","engine_version","seats","seeds","sampler_seeds","rotations","match_count","schedule_cap","max_inflight","max_record_bytes","max_decisions",...Object.keys(fit.properties).map(k=>"fit."+k)];
  for(const path of fields){expect(control(root,path),path).not.toBeNull();expect(control(root,path).closest("label")!.querySelector("small")!.textContent).toBeTruthy();expect(control(root,path).value).toBe("");}
  const data={name:"Chosen schedule",config:{game_id:"fixture",variant:"standard",players:1},rules_version:"r1",engine_version:"e1",seats:["fixture-bot"],seeds:[9],sampler_seeds:[[8]],rotations:[0],match_count:1,schedule_cap:1,max_inflight:1,max_record_bytes:900,max_decisions:10};
  const json=root.querySelector<HTMLTextAreaElement>('[data-json="tournament"]')!;expect(json).toBeTruthy();json.value=JSON.stringify(data);click(root,"Import tournament JSON");await settle();
  expect(control(root,"name").value).toBe("Chosen schedule");
  expect(JSON.parse(root.querySelector('[data-summary="tournament"]')!.textContent!)).toEqual(data);
  root.querySelector("form")!.dispatchEvent(new Event("submit",{cancelable:true}));await settle();
  expect(calls.find(c=>c.path==="/api/tournaments"&&c.body)?.body).toEqual(data);
  expect(window.sessionStorage.length).toBe(0);expect(window.localStorage.length).toBe(0);
  expect(root.textContent).not.toContain("private-capability");
});

test("late reads cannot replace a newly selected tournament",async()=>{
  let finish!:(v:any)=>void;
  const {root}=setup(path=>path==="/api/tournaments/old"?new Promise(resolve=>{finish=resolve;}):undefined);await settle();
  const id=root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!;
  id.value="old";click(root,"Read saved tournament");id.value="new";click(root,"Read saved tournament");await settle();finish(saved("old"));await settle();
  expect(Array.from(root.querySelectorAll("pre")).find(p=>p.textContent?.includes('"tournament_id"'))!.textContent).toContain('"tournament_id": "new"');
});

test("mutation pins revision and spec and errors never echo the creator credential",async()=>{
  const {root,calls}=setup((path)=>path.endsWith("/start")?Promise.reject(new Error("Bearer secret-credential")):undefined);await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!.value="t1";click(root,"Read saved tournament");await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value="secret-credential";
  click(root,"Start");await settle();
  expect(calls.find(c=>c.path.endsWith("/start"))).toEqual({path:"/api/tournaments/t1/start",body:{revision:7,spec_sha:"a".repeat(64)},credential:"secret-credential"});
  expect(root.textContent).not.toContain("secret-credential");
});

test("dispose ignores pending discovery and status responses",async()=>{
  let finish!:(v:any)=>void;
  const {root,ui}=setup(path=>path.endsWith("/schema")?new Promise(resolve=>{finish=resolve;}):undefined);
  ui.dispose();finish({tournament,fit});await settle();expect(root.children.length).toBe(0);
});



test("fit JSON remains optional for play and round trips only after explicit fit",async()=>{
  const {root,calls}=setup((path)=>path.endsWith("/start")?saved():path.endsWith("/fit")?{key:{players:1},points:{policy:[4]},ratings:[],components:[],observed_games:1}:undefined);await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!.value="t1";click(root,"Read saved tournament");await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value="secret";
  click(root,"Start");await settle();expect(calls.filter(c=>c.path.endsWith("/start"))).toHaveLength(1);
  const settings={gauge:"component_sum_zero",elo_origin:0,ridge_lambda:0,prior_pseudocount:0,solver:"newton_backtracking",max_iterations:20,max_backtracks:8,gradient_inf_tolerance:0.001,armijo_coefficient:0.1,backtrack_factor:0.5};
  const codec=root.querySelector<HTMLTextAreaElement>('[data-json="fit"]')!;codec.value=JSON.stringify(settings);click(root,"Import fit JSON");await settle();
  expect(JSON.parse(root.querySelector('[data-summary="fit"]')!.textContent!)).toEqual(settings);
  expect(calls.some(c=>c.path.endsWith("/fit"))).toBe(false);
  root.querySelectorAll("form")[1].dispatchEvent(new Event("submit",{cancelable:true}));await settle();
  expect(calls.find(c=>c.path.endsWith("/fit"))?.body).toEqual({revision:7,fit:settings});
  expect(root.textContent).toContain("Solo: points and counts only");
});

test("late JSON import cannot mutate controls after dispose",async()=>{
  let finish!:(v:any)=>void;
  const {root,ui}=setup(path=>path.endsWith("/bots")?new Promise(resolve=>{finish=resolve;}):undefined);await settle();
  const json=root.querySelector<HTMLTextAreaElement>('[data-json="tournament"]')!;
  json.value=JSON.stringify({name:"Chosen",config:{game_id:"fixture",variant:"standard",players:1},seats:["fixture-bot"],seeds:[1],rules_version:"r1",engine_version:"e1"});
  const name=control(root,"name");click(root,"Import tournament JSON");ui.dispose();finish([{choice_id:"fixture-bot",display_name:"Fixture bot"}]);await settle();
  expect(control(root,"name")).toBeNull();
  expect(name.value).toBe("");
});



test("all explicit lifecycle actions use the latest saved revision and restart pins",async()=>{
  let revision=7;
  const {root,calls}=setup(path=>/\/(start|stop|restart|delete)$/.test(path)?{...saved(),revision:++revision,status:path.endsWith("delete")?"deleted":"stopped"}:undefined);await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!.value="t1";click(root,"Read saved tournament");await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value="secret";
  for(const action of ["Start","Stop","Restart","Delete"]){click(root,action);await settle();}
  expect(calls.filter(c=>c.body!==undefined).map(c=>({path:c.path,body:c.body}))).toEqual([
    {path:"/api/tournaments/t1/start",body:{revision:7,spec_sha:"a".repeat(64)}},
    {path:"/api/tournaments/t1/stop",body:{revision:8}},
    {path:"/api/tournaments/t1/restart",body:{revision:9,spec_sha:"a".repeat(64)}},
    {path:"/api/tournaments/t1/delete",body:{revision:10}},
  ]);
  expect(root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value).toBe("");
});

test("canonical replay is read only, uses permitted frames and disposes on selection",async()=>{
  let finish!:(v:any)=>void;
  const frame={position:0,total_positions:2,observation:{phase:"fixture"},events:[{kind:"fixture"}]};
  const {root,calls}=setup(path=>{
    if(path==="/api/tournaments/t1")return {...saved(),status:"completed",matches:[{match_id:"m1",ordinal:0,status:"completed",result:{identities:["rotated-policy"]}}]};
    if(path.endsWith("/board"))return {fixture:true};if(path.endsWith("/rules"))return "Canonical fixture rules";
    if(path.endsWith("position=0"))return frame;
    if(path.endsWith("position=1"))return new Promise(resolve=>{finish=resolve;});
  });await settle();
  const id=root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!;id.value="t1";click(root,"Read saved tournament");await settle();click(root,"Replay match 1");await settle();
  expect(viewPort.mount.mock.calls[0][1]).toEqual({gameId:"fixture",boardSpec:{fixture:true},rulesText:"Canonical fixture rules",settings:{table:{properties:{}},live:{properties:{}},simulation_defaults:{},bots:{}},readOnly:true,onExit:expect.any(Function),fullscreenRoot:expect.any(HTMLElement)});
  const stage = viewPort.mount.mock.calls[0][1].fullscreenRoot;
  expect(stage.contains(viewPort.mount.mock.calls[0][0])).toBe(true);
  expect(stage.contains([...root.querySelectorAll("button")].find(b => b.textContent === "Next"))).toBe(true);
  expect(viewPort.update.mock.calls[0][0]).toMatchObject({status:"replay",observation:frame.observation,seats:[{display_name:"rotated-policy",seat:0}]});
  expect(viewPort.update.mock.calls[0][1]).toEqual(frame.events);
  click(root,"Next");id.value="new";id.dispatchEvent(new Event("input"));finish({...frame,position:1});await settle();
  expect(viewPort.update).toHaveBeenCalledTimes(1);expect(viewPort.dispose).toHaveBeenCalledTimes(1);
  expect(calls.some(c=>c.path.includes("record")||c.body!==undefined)).toBe(false);
});

test("invalid arrays and unknown JSON fields never reach creation",async()=>{
  const {root,calls}=setup();await settle();
  const codec=root.querySelector<HTMLTextAreaElement>('[data-json="tournament"]')!;
  codec.value=JSON.stringify({name:"Chosen",config:{game_id:"fixture",variant:"standard",players:1},seats:["fixture-bot"],seeds:[1],unknown:"private"});click(root,"Import tournament JSON");await settle();
  expect(control(root,"name").value).toBe("");
  value(root,"seeds","[invalid");root.querySelector("form")!.dispatchEvent(new Event("submit",{cancelable:true}));await settle();
  expect(calls.filter(c=>c.body!==undefined)).toEqual([]);
});

test("late failed read cannot overwrite status for a new selection",async()=>{
  let reject!:(e:Error)=>void;
  const {root}=setup(path=>path==="/api/tournaments/old"?new Promise((_resolve,fail)=>{reject=fail;}):undefined);await settle();
  const id=root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!;
  id.value="old";click(root,"Read saved tournament");id.value="new";click(root,"Read saved tournament");await settle();
  const status=root.querySelector('[role="status"]')!.textContent;
  reject(new Error("Old request failed"));await settle();
  expect(root.querySelector('[role="status"]')!.textContent).toBe(status);
});

test("choosing a game binds visible read-only versions and changing game clears seat choices",async()=>{
  const {root}=setup(path=>path==="/api/games"?[...games,{...games[0],game_id:"second",name:"Second game",rules_version:"r2",engine_version:"e2"}]:undefined);await settle();
  expect(control(root,"rules_version").value).toBe("");expect(control(root,"engine_version").value).toBe("");
  value(root,"config.game_id","fixture");await settle();
  expect(control(root,"rules_version").value).toBe("r1");expect(control(root,"engine_version").value).toBe("e1");
  expect((control(root,"rules_version") as HTMLInputElement).readOnly).toBe(true);expect((control(root,"engine_version") as HTMLInputElement).readOnly).toBe(true);
  value(root,"config.players","1");value(root,"seats",'["fixture-bot"]');
  value(root,"config.game_id","second");await settle();
  expect(control(root,"rules_version").value).toBe("r2");expect(control(root,"engine_version").value).toBe("e2");expect(control(root,"seats").value).toBe("");
  expect(JSON.parse(root.querySelector('[data-summary="tournament"]')!.textContent!)).toEqual({config:{game_id:"second"},rules_version:"r2",engine_version:"e2"});
  for(const path of ["config.variant","config.players","seeds","match_count","fit.gauge","fit.solver"])expect(control(root,path).value,path).toBe("");
  value(root,"config.game_id","");await settle();expect(control(root,"rules_version").value).toBe("");expect(control(root,"engine_version").value).toBe("");
});

test.each(["rules_version","engine_version"])("JSON import rejects a mismatched %s without rewriting versions or loading bots",async key=>{
  const {root,calls}=setup();await settle();
  const data={name:"Imported",config:{game_id:"fixture",variant:"standard",players:1},seats:["fixture-bot"],seeds:[1],rules_version:"r1",engine_version:"e1",[key]:"other-version"};
  root.querySelector<HTMLTextAreaElement>('[data-json="tournament"]')!.value=JSON.stringify(data);click(root,"Import tournament JSON");await settle();
  expect(root.querySelector('[role="status"]')!.textContent).toContain("does not match");
  expect(control(root,"config.game_id").value).toBe("");expect(control(root,"rules_version").value).toBe("");expect(control(root,"engine_version").value).toBe("");
  expect(calls.some(c=>c.path.endsWith("/bots")||c.body!==undefined)).toBe(false);
});


async function fillSchedule(root:HTMLElement){
  root.querySelector<HTMLTextAreaElement>('[data-json="tournament"]')!.value=JSON.stringify({name:"Fixture schedule",config:{game_id:"fixture",variant:"standard",players:1},rules_version:"r1",engine_version:"e1",seats:["fixture-bot"],seeds:[1]});
  click(root,"Import tournament JSON");await settle();
}

test("double Save creates only one pending tournament and keeps its creator capability",async()=>{
  let finish!:(v:any)=>void;
  const {root,calls}=setup((path,body)=>path==="/api/tournaments"&&body?new Promise(resolve=>{finish=resolve;}):undefined);await settle();await fillSchedule(root);
  const form=root.querySelector("form")!;
  form.dispatchEvent(new Event("submit",{cancelable:true}));form.dispatchEvent(new Event("submit",{cancelable:true}));
  expect(calls.filter(c=>c.path==="/api/tournaments"&&c.body)).toHaveLength(1);
  expect(Array.from(form.querySelectorAll("button")).find(b=>b.textContent==="Save tournament")!.disabled).toBe(true);
  finish({...saved("created"),creator_credential:"created-secret"});await settle();
  expect(root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value).toBe("created-secret");
  expect(Array.from(form.querySelectorAll("button")).find(b=>b.textContent==="Save tournament")!.disabled).toBe(false);
  expect(calls.some(c=>/\/(start|restart|fit)$/.test(c.path))).toBe(false);
  expect(window.sessionStorage.length).toBe(0);expect(window.localStorage.length).toBe(0);expect(root.textContent).not.toContain("created-secret");
});

test.each([false,true])("sequential successful creates preserve both capabilities when selection changes: %s",async changeSelection=>{
  const pending:Array<(v:any)=>void>=[];
  const {root,calls}=setup((path,body)=>path==="/api/tournaments"&&body?new Promise(resolve=>pending.push(resolve)):undefined);await settle();await fillSchedule(root);
  const form=root.querySelector("form")!, id=root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!;
  for(const name of ["first","second"]){
    form.dispatchEvent(new Event("submit",{cancelable:true}));
    if(changeSelection){id.value="other";id.dispatchEvent(new Event("input"));}
    pending.shift()!({...saved(name),creator_credential:`${name}-secret`});await settle();
    if(changeSelection)expect(id.value).toBe("other");
  }
  expect(calls.filter(c=>c.path==="/api/tournaments"&&c.body)).toHaveLength(2);
  for(const name of ["first","second"]){
    id.value=name;id.dispatchEvent(new Event("input"));click(root,"Read saved tournament");await settle();click(root,"Start");await settle();
    expect(calls.find(c=>c.path===`/api/tournaments/${name}/start`)?.credential).toBe(`${name}-secret`);
  }
  expect(window.sessionStorage.length).toBe(0);expect(window.localStorage.length).toBe(0);
  expect(root.textContent).not.toContain("first-secret");expect(root.textContent).not.toContain("second-secret");
});

test("failed create unlocks Save but never retries until a new explicit submit",async()=>{
  let fail!:(e:Error)=>void;
  const {root,calls}=setup((path,body)=>path==="/api/tournaments"&&body?new Promise((_resolve,reject)=>{fail=reject;}):undefined);await settle();await fillSchedule(root);
  const form=root.querySelector("form")!;form.dispatchEvent(new Event("submit",{cancelable:true}));
  fail(new Error("Fixture failure"));await settle();
  expect(calls.filter(c=>c.path==="/api/tournaments"&&c.body)).toHaveLength(1);
  expect(Array.from(form.querySelectorAll("button")).find(b=>b.textContent==="Save tournament")!.disabled).toBe(false);
  form.dispatchEvent(new Event("submit",{cancelable:true}));expect(calls.filter(c=>c.path==="/api/tournaments"&&c.body)).toHaveLength(2);
  fail(new Error("Second fixture failure"));await settle();
});

test("unconfirmed cleanup retains ownership and latest revision for an explicit Delete retry",async()=>{
  let state={...saved("owned"),cleanup_unconfirmed:false};let deletes=0;
  const {root,calls}=setup((path,body)=>{
    if(path==="/api/tournaments"&&body)return {...state,creator_credential:"owned-secret"};
    if(path==="/api/tournaments/owned")return state;
    if(path==="/api/tournaments/owned/delete")return state={...state,revision:deletes++===0?9:11,status:deletes===1?"cleanup_unconfirmed":"deleted",cleanup_unconfirmed:deletes===1};
  });await settle();await fillSchedule(root);root.querySelector("form")!.dispatchEvent(new Event("submit",{cancelable:true}));await settle();
  click(root,"Delete");await settle();
  const credential=root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!,id=root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!;
  expect(credential.value).toBe("owned-secret");expect(root.querySelector('[role="status"]')!.textContent).toContain("cleanup_unconfirmed");
  expect(calls.filter(c=>c.path.endsWith("/delete"))).toHaveLength(1);
  id.value="other";id.dispatchEvent(new Event("input"));id.value="owned";id.dispatchEvent(new Event("input"));click(root,"Read saved tournament");await settle();
  expect(credential.value).toBe("owned-secret");click(root,"Delete");await settle();
  expect(calls.filter(c=>c.path.endsWith("/delete")).map(c=>({body:c.body,credential:c.credential}))).toEqual([{body:{revision:7},credential:"owned-secret"},{body:{revision:9},credential:"owned-secret"}]);
  expect(credential.value).toBe("");id.value="other";id.dispatchEvent(new Event("input"));id.value="owned";id.dispatchEvent(new Event("input"));expect(credential.value).toBe("");
  expect(window.sessionStorage.length).toBe(0);expect(window.localStorage.length).toBe(0);expect(root.textContent).not.toContain("owned-secret");
});

test.each([
  {status:"deleting",cleanup_unconfirmed:false},{status:"cleanup_unconfirmed",cleanup_unconfirmed:true},
  {status:"stopped",cleanup_unconfirmed:false},{status:"unknown",cleanup_unconfirmed:false},
  {status:"deleted",cleanup_unconfirmed:true},{status:"deleted",cleanup_unconfirmed:undefined},
])("Delete preserves ownership until terminal cleanup is explicitly confirmed: %j",async result=>{
  const {root,calls}=setup((path,body)=>path==="/api/tournaments"&&body?{...saved("owned"),creator_credential:"owned-secret"}:path.endsWith("/delete")?{...saved("owned"),revision:8,...result}:undefined);await settle();await fillSchedule(root);root.querySelector("form")!.dispatchEvent(new Event("submit",{cancelable:true}));await settle();
  click(root,"Delete");await settle();
  expect(root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value).toBe("owned-secret");
  expect(calls.filter(c=>c.path.endsWith("/delete"))).toHaveLength(1);click(root,"Delete");await settle();
  expect(calls.filter(c=>c.path.endsWith("/delete"))[1].body).toEqual({revision:8});
  const id=root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!;id.value="other";id.dispatchEvent(new Event("input"));id.value="owned";id.dispatchEvent(new Event("input"));expect(root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value).toBe("owned-secret");
});

test.each([{}, {...saved("other"),status:"deleted",cleanup_unconfirmed:false}])("unrecognized Delete response does not replace ownership or saved selection: %j",async result=>{
  const {root,calls}=setup((path,body)=>path==="/api/tournaments"&&body?{...saved("owned"),creator_credential:"owned-secret"}:path.endsWith("/delete")?result:undefined);await settle();await fillSchedule(root);root.querySelector("form")!.dispatchEvent(new Event("submit",{cancelable:true}));await settle();
  click(root,"Delete");await settle();expect(root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!.value).toBe("owned-secret");click(root,"Delete");await settle();
  expect(calls.filter(c=>c.path.endsWith("/delete")).map(c=>c.body)).toEqual([{revision:7},{revision:7}]);
});

test("late Delete response cannot clear another selected tournament creator capability",async()=>{
  let finish!:(v:any)=>void,created=0;
  const {root,calls}=setup((path,body)=>{
    if(path==="/api/tournaments"&&body){const name=++created===1?"first":"second";return {...saved(name),creator_credential:`${name}-secret`};}
    if(path==="/api/tournaments/first/delete")return new Promise(resolve=>{finish=resolve;});
  });await settle();await fillSchedule(root);
  for(let i=0;i<2;i++){root.querySelector("form")!.dispatchEvent(new Event("submit",{cancelable:true}));await settle();}
  const id=root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!,credential=root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!;
  id.value="first";id.dispatchEvent(new Event("input"));click(root,"Read saved tournament");await settle();click(root,"Delete");
  id.value="second";id.dispatchEvent(new Event("input"));click(root,"Read saved tournament");await settle();finish({...saved("first"),revision:9,status:"deleted",cleanup_unconfirmed:false});await settle();
  expect(credential.value).toBe("second-secret");click(root,"Start");await settle();expect(calls.find(c=>c.path==="/api/tournaments/second/start")?.credential).toBe("second-secret");
});



test.each(["seek","replay"])("stale failed %s cannot replace a newer replay status or frame",async operation=>{
  const pending:Array<{resolve:(value:any)=>void,reject:(error:Error)=>void}>=[];
  const frame={position:0,total_positions:3,observation:{phase:"initial"},events:[]};
  let initial=operation==="seek";
  const {root,calls}=setup(path=>{
    if(path==="/api/tournaments/t1")return {...saved(),status:"completed",matches:[{match_id:"m1",ordinal:0,status:"completed",result:{identities:["policy"]}}]};
    if(path.endsWith("/board"))return {fixture:true};if(path.endsWith("/rules"))return "Canonical fixture rules";
    if(path.includes("/replay?")){if(initial){initial=false;return frame;}return new Promise((resolve,reject)=>pending.push({resolve,reject}));}
  });await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!.value="t1";click(root,"Read saved tournament");await settle();
  const credential=root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!;credential.value="fixture-secret";
  if(operation==="seek"){click(root,"Replay match 1");await settle();}
  for(let i=0;i<2;i++){click(root,operation==="seek"?"Next":"Replay match 1");if(operation==="replay")await vi.dynamicImportSettled();}
  await vi.dynamicImportSettled();
  pending[1].resolve({...frame,position:1,observation:{phase:"newer"}});await settle();
  await vi.waitFor(()=>expect(root.textContent).toContain("Replay 2/3"));
  const status=root.querySelector('[role="status"]')!.textContent,updates=viewPort.update.mock.calls.length;
  pending[0].reject(new Error("Stale fixture failure"));await settle();
  expect(root.querySelector('[role="status"]')!.textContent).toBe(status);
  expect(root.textContent).toContain("Replay 2/3");expect(viewPort.update).toHaveBeenCalledTimes(updates);
  expect(viewPort.update.mock.calls.at(-1)![0].observation).toEqual({phase:"newer"});
  expect(credential.value).toBe("fixture-secret");expect(root.textContent).not.toContain("fixture-secret");
  expect(calls.some(call=>call.body!==undefined)).toBe(false);
});

test("stale failed discovery cannot replace the status or bots after a newer game succeeds",async()=>{
  let reject!:(error:Error)=>void;
  const {root}=setup(path=>{
    if(path==="/api/games")return [...games,{...games[0],game_id:"second",name:"Second game",rules_version:"r2",engine_version:"e2"}];
    if(path==="/api/games/fixture/bots")return new Promise((_resolve,fail)=>{reject=fail;});
    if(path==="/api/games/second/bots")return [{choice_id:"second-bot",display_name:"Second bot",kind:"builtin",reference:{}}];
  });await settle();
  value(root,"config.game_id","fixture");value(root,"config.game_id","second");await settle();value(root,"config.players","1");
  const status=root.querySelector('[role="status"]')!.textContent;
  const credential=root.querySelector<HTMLInputElement>('[aria-label="Creator credential"]')!;credential.value="fixture-secret";
  expect(root.querySelector('[data-seat="0"]')!.textContent).toContain("Second bot");
  reject(new Error("Old game failed"));await settle();
  expect(root.querySelector('[role="status"]')!.textContent).toBe(status);
  expect(control(root,"config.game_id").value).toBe("second");expect(control(root,"rules_version").value).toBe("r2");
  expect(root.querySelector('[data-seat="0"]')!.textContent).toContain("Second bot");
  expect(root.querySelector('[data-seat="0"]')!.textContent).not.toContain("Fixture bot");expect(credential.value).toBe("fixture-secret");
});

test.each(["seek","replay","discovery"])("current %s failure stays visible and a disposed failure cannot alter detached DOM",async operation=>{
  let reject!:(error:Error)=>void,initial=true;
  const {root,ui}=setup(path=>{
    if(path==="/api/tournaments/t1")return {...saved(),status:"completed",matches:[{match_id:"m1",ordinal:0,status:"completed",result:{identities:["policy"]}}]};
    if(path.endsWith("/board"))return {fixture:true};if(path.endsWith("/rules"))return "Canonical fixture rules";
    if(path.endsWith("/bots")&&operation==="discovery")return new Promise((_resolve,fail)=>{reject=fail;});
    if(path.includes("/replay?")){
      if(initial&&operation==="seek"){initial=false;return {position:0,total_positions:3,observation:{phase:"initial"},events:[]};}
      return new Promise((_resolve,fail)=>{reject=fail;});
    }
  });await settle();
  root.querySelector<HTMLInputElement>('[aria-label="Tournament ID"]')!.value="t1";click(root,"Read saved tournament");await settle();
  if(operation==="seek"){click(root,"Replay match 1");await settle();}
  const request=()=>{if(operation==="discovery")value(root,"config.game_id","fixture");else click(root,operation==="seek"?"Next":"Replay match 1");};
  request();reject(new Error("Current fixture failure"));await settle();
  const status=root.querySelector('[role="status"]')!;expect(status.textContent).toContain("could not be completed");
  request();ui.dispose();status.textContent="Detached status sentinel";const updates=viewPort.update.mock.calls.length;
  reject(new Error("Disposed fixture failure"));await settle();
  expect(status.textContent).toBe("Detached status sentinel");expect(root.children.length).toBe(0);expect(viewPort.update).toHaveBeenCalledTimes(updates);
});

test("uses module simulation defaults, preserves explicit unlimited and configured entrants when saving",async()=>{
  const schema={properties:{rounds:{type:["integer","null"],minimum:1,default:null,title:"Round cap"}}};
  const botSchema={properties:{risk:{type:"number",default:1,title:"Risk"}}};
  const {root,calls}=setup(path=>path.endsWith("/settings")?{table:schema,simulation_defaults:{rounds:137},live:{},bots:{"fixture-bot":botSchema}}:undefined);
  await settle();value(root,"config.game_id","fixture");await settle();
  const cap=root.querySelector<HTMLInputElement>('[aria-label="Round cap"]');
  expect(cap?.value).toBe("137");
  cap!.value="";cap!.dispatchEvent(new Event("input"));
  value(root,"config.variant","standard");value(root,"config.players","1");
  const seat=root.querySelector<HTMLSelectElement>("[data-seat]")!;
  seat.value="fixture-bot";seat.dispatchEvent(new Event("change"));
  const risk=root.querySelector<HTMLInputElement>('[aria-label="Risk"]')!;
  risk.value="2";risk.dispatchEvent(new Event("input"));
  value(root,"name","Fixture");value(root,"seeds","[1]");
  click(root,"Save tournament");await settle();
  const submitted=calls.find(c=>c.path==="/api/tournaments" && c.body)?.body;
  expect(submitted.config.settings).toEqual({rounds:null});
  expect(submitted.seats).toEqual([{choice_id:"fixture-bot",options:{risk:2}}]);
});

test("refuses a malformed current entrant draft and submits its corrected options", async () => {
  const schema = { properties: { bonuses: { type: "array", title: "Station bonuses", default: [] } } };
  const { root, calls } = setup(path => path.endsWith("/settings") ? { table: {}, live: {}, simulation_defaults: {}, bots: { "fixture-bot": schema } } : undefined);
  await settle(); value(root, "config.game_id", "fixture"); await settle();
  value(root, "config.variant", "standard"); value(root, "config.players", "1");
  value(root, "name", "Draft"); value(root, "seeds", "[1]");
  const seat = root.querySelector<HTMLSelectElement>("[data-seat]")!;
  seat.value = "fixture-bot"; seat.dispatchEvent(new Event("change"));
  const draft = root.querySelector<HTMLTextAreaElement>('[aria-label="Station bonuses"]')!;
  const type = (s: string) => { draft.value = s; draft.dispatchEvent(new Event("input")); };
  const posts = () => calls.filter(c => c.path === "/api/tournaments" && c.body);
  type("[9]"); type("[1,"); click(root, "Save tournament"); await settle();
  expect(posts()).toHaveLength(0);
  expect(draft.value).toBe("[1,");
  type("[7]"); click(root, "Save tournament"); await settle();
  expect(posts().map(c => c.body.seats)).toEqual([[{ choice_id: "fixture-bot", options: { bonuses: [7] } }]]);
});

test("retains a misspelled imported bot option for server validation and saves the exact corrected schedule", async () => {
  const bot = { additionalProperties: false, properties: { min_reserve: { type: "integer", default: 100 }, bonuses: { type: "array", default: [] } } };
  const rejected = vi.fn();
  const { root, calls } = setup((path, body) => {
    if (path.endsWith("/settings")) return { table: {}, simulation_defaults: {}, live: {}, bots: { "fixture-bot": bot } };
    if (path === "/api/tournaments" && body) {
      if (body.seats[0].options && "min_resevre" in body.seats[0].options) {
        rejected();
        throw new Error("Unknown settings field: min_resevre");
      }
      return { ...saved(), creator_credential: "test-creator" };
    }
  });
  await settle();
  const schedule = { name: "Explicit schedule", config: { game_id: "fixture", variant: "standard", players: 2 },
    rules_version: "r1", engine_version: "e1", seeds: [9, 11], match_count: 2 };
  const entered = { ...schedule, seats: [{ choice_id: "fixture-bot", options: { min_resevre: 421, bonuses: [2, 7] } }, "fixture-bot"] };
  const corrected = { ...schedule, seats: [{ choice_id: "fixture-bot", options: { min_reserve: 421, bonuses: [2, 7] } }, "fixture-bot"] };
  async function importAndSave(data: typeof entered | typeof corrected) {
    root.querySelector<HTMLTextAreaElement>('[data-json="tournament"]')!.value = JSON.stringify(data);
    click(root, "Import tournament JSON"); await settle();
    expect(JSON.parse(root.querySelector('[data-summary="tournament"]')!.textContent!)).toEqual(data);
    click(root, "Save tournament"); await settle();
  }
  const posts = () => calls.filter(c => c.path === "/api/tournaments" && c.body).map(c => c.body);
  await importAndSave(entered);
  expect(posts()).toEqual([entered]);
  expect(rejected).toHaveBeenCalledOnce();
  expect(root.querySelector('[role="status"]')!.textContent).toContain("could not be completed");
  await importAndSave(corrected);
  expect(posts()).toEqual([entered, corrected]);
  expect(rejected).toHaveBeenCalledOnce();
});

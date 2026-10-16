import { expect, it } from "vitest";
import { initialState, reduce } from "./session";
import type { ExtensionSurface } from "../port/wire";

const live: ExtensionSurface = { pluginId: "fixture", surfaceId: "view", sessionId: "s1", generation: 3, kind: "view", view: { body: [{kind:"text",value:"retained view"}] } };
it("restores a standing view and retires its old controls explicitly", () => {
 let s=reduce(initialState,{kind:"extension_surface",extension:live});
 expect(s.views).toEqual([live]);
 s=reduce(s,{kind:"extension_surface",extension:{...live,code:"surface.expired",view:undefined}});
 expect(s.views).toEqual([]);
 expect(s.items.filter(i=>i.t==="extension")).toMatchObject([{ext:{code:"surface.expired"}}]);
 expect(s.entranceOwed).toEqual([]);
 s=reduce(s,{kind:"extension_surface",extension:live});
 expect(s.views).toEqual([live]);
 expect(s.items.filter(i=>i.t==="extension")).toEqual([]);
});
it("keeps interaction counts content-free and does not count replay twice",()=>{
 const ask={id:"question",questions:[{id:"q",prompt:"PRIVATE",options:[{label:"A"},{label:"B"}]}]};
 let s=reduce(initialState,{kind:"ask_request",ask});
 s=reduce(s,{kind:"ask_request",ask});
 const item=s.items.find(i=>i.t==="ask")!;
 s=reduce(s,{kind:"__decided",id:item.id,answers:[["private custom answer"]]});
 expect(s.interactions).toEqual({requests:1,questions:1,options:2,answers:1,customAnswers:1});
 expect(JSON.stringify(s.interactions)).not.toContain("private");
 s=reduce(s,{kind:"__decided",id:item.id,answers:[["private custom answer"]]});
 expect(s.interactions.answers).toBe(1);
});

it("recovers a card without replaying its entrance and preserves view ordering", () => {
  const card: ExtensionSurface = {pluginId:"fixture",surfaceId:"card",kind:"card",card:{title:"Restored"}};
  let s=reduce(initialState,{kind:"__extension_snapshot",surfaces:[card,live,{...live,surfaceId:"other"}]});
  expect(s.entranceOwed).toEqual([]);
  const id=s.items[0].id;
  s=reduce(s,{kind:"__extension_snapshot",surfaces:[card]});
  expect(s.items[0].id).toBe(id);
  s=reduce(s,{kind:"extension_surface",extension:{...live,view:{body:[{kind:"text",value:"updated"}]}}});
  expect(s.views.map(v=>v.surfaceId)).toEqual(["view","other"]);
});

it("standing view updates leave transcript revision and pending entrances alone", () => {
  let s = reduce(initialState, {kind:"extension_surface",extension:live});
  const items = s.items;
  const revision = s.revision;
  s = reduce(s, {kind:"extension_surface",extension:{...live,view:{body:[{kind:"text",value:"new"}]}}});
  expect(s.items).toBe(items);
  expect(s.revision).toBe(revision);
  const card: ExtensionSurface = {pluginId:"fixture",surfaceId:"card",kind:"card",card:{title:"Live"}};
  s = reduce(s, {kind:"extension_surface",extension:card});
  s = reduce(s, {kind:"__extension_snapshot",surfaces:[{...card,code:"surface.expired",card:undefined}]});
  expect(s.entranceOwed).toEqual([]);
});

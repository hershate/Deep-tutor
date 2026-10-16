// @vitest-environment jsdom
import "./testkit";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useReducer } from "react";
import { useSurfaceReplay } from "./surface-replay";
import { ExtensionCard } from "./cards/ExtensionCard";
import { initialState, reduce } from "../state/session";
import { MockPort } from "../port/mock";
import { HttpError } from "../port/port";
import type { ExtensionSurface, WireEvent } from "../port/wire";

afterEach(cleanup);
const live:ExtensionSurface={pluginId:"fixture",surfaceId:"card",kind:"card",card:{title:"Restored card",text:"Retained publication",actions:[{actionId:"run",label:"Run fixture"}]}};
it("restores a fresh window without entrance motion or re-asking",async()=>{
 const port=new MockPort();
 vi.spyOn(port,"extensionSurfaces").mockResolvedValue({surfaces:[live]});
 const onInvoke=vi.fn();
 function Window(){
  const [s,dispatch]=useReducer(reduce,initialState);
  useSurfaceReplay(port,dispatch);
  return <><output data-testid="entrances">{s.entranceOwed.length}</output>{s.items.map(i=>i.t==="extension"?<ExtensionCard key={i.id} ext={i.ext} onInvoke={onInvoke}/>:null)}</>;
 }
 const view=render(<Window/>);
 await screen.findByText("Retained publication");
 expect(screen.getByTestId("entrances").textContent).toBe("0");
 expect(screen.getByRole("button",{name:"Run fixture"})).toBeTruthy();
 view.unmount();
 vi.mocked(port.extensionSurfaces).mockResolvedValue({surfaces:[{...live,code:"surface.expired",card:undefined}]});
 render(<Window/>);
 const expired = await screen.findByText("扩展界面已失效");
 expect(expired.closest('[role="status"]')?.querySelector(".line")).toBeNull();
 expect(expired.closest('[role="status"]')?.getAttribute("data-code")).toBe("surface.expired");
 expect(screen.queryByRole("button",{name:"Run fixture"})).toBeNull();
 expect(onInvoke).not.toHaveBeenCalled();
 expect(screen.getByTestId("entrances").textContent).toBe("0");
});
it("a delayed snapshot cannot replace a publication received during its read",async()=>{
 const port=new MockPort();let done!:(p:{surfaces:ExtensionSurface[]})=>void;
 vi.spyOn(port,"extensionSurfaces").mockReturnValue(new Promise(resolve=>{done=resolve}));
 let emit!:(e:WireEvent)=>void;
 function Window(){
  const [s,dispatch]=useReducer(reduce,initialState);
  const replay=useSurfaceReplay(port,dispatch);
  emit=(e)=>{replay.observe(e);dispatch(e)};
  return <>{s.items.map(i=>i.t==="extension"?<ExtensionCard key={i.id} ext={i.ext}/>:null)}</>;
 }
 render(<Window/>);
 act(()=>emit({kind:"extension_surface",extension:{...live,card:{title:"New publication"}}}));
 await act(async()=>done({surfaces:[live]}));
 expect(screen.getByText("New publication")).toBeTruthy();
 expect(screen.queryByText("Restored card")).toBeNull();
});
it("a removed window cannot receive a late snapshot or failure",async()=>{
 const port=new MockPort();let done!:(p:{surfaces:ExtensionSurface[]})=>void;
 vi.spyOn(port,"extensionSurfaces").mockReturnValue(new Promise(resolve=>{done=resolve}));
 const dispatch=vi.fn();
 function Window(){useSurfaceReplay(port,dispatch);return null;}
 const view=render(<Window/>);view.unmount();
 await act(async()=>done({surfaces:[live]}));
 expect(dispatch).not.toHaveBeenCalled();
});

it("says nothing for a restored window the kernel calls complete, and one line when it dropped a surface",async()=>{
 const port=new MockPort();
 const dispatch=vi.fn();
 function Window(){useSurfaceReplay(port,dispatch);return null;}
 vi.spyOn(port,"extensionSurfaces").mockResolvedValue({surfaces:[]});
 const quiet=render(<Window/>);
 await act(async()=>{});
 expect(dispatch.mock.calls.filter(([e])=>e.kind==="__error")).toEqual([]);
 quiet.unmount();dispatch.mockClear();
 vi.mocked(port.extensionSurfaces).mockResolvedValue({surfaces:[live],incomplete:"dropped"});
 render(<Window/>);
 await act(async()=>{});
 const errors=dispatch.mock.calls.filter(([e])=>e.kind==="__error").map(([e])=>e.text);
 expect(errors).toEqual(["部分扩展界面可能没能恢复"]);
 expect(dispatch.mock.calls.some(([e])=>e.kind==="__extension_snapshot"&&e.surfaces.length===1)).toBe(true);
});
it("words a real read failure once, from its code",async()=>{
 const port=new MockPort();
 const dispatch=vi.fn();
 vi.spyOn(port,"extensionSurfaces").mockRejectedValue(new HttpError(500,"unreadable",{code:"surface.replay_unreadable",error:"extension surface history is unreadable"},true));
 function Window(){useSurfaceReplay(port,dispatch);return null;}
 render(<Window/>);
 await act(async()=>{});
 const text=dispatch.mock.calls.find(([e])=>e.kind==="__error")![0].text as string;
 expect(text).toBe("扩展界面的历史记录读不出来，已有的界面可能没有恢复");
 expect(text.match(/扩展界面/g)).toHaveLength(1);
});

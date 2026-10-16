// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import SurfaceActionCard from "./a2ui/action";
import type {SurfaceActionRecord,SurfaceActionRequest} from "../../port/surface";
import type {SurfaceSpec} from "./spec";

afterEach(cleanup);
const spec:SurfaceSpec={spec_version:1,title:"Draft",body:[{kind:"editable_text",key:"draft",label:"Draft text",field:{type:"string",required:true,default:"initial"}},{kind:"button",label:"Continue",actionId:"continue"}]};
const initial:SurfaceActionRecord={active:true,action:{sessionId:"session",surfaceId:"surface",callId:"call",requestId:"",actionId:"",values:{} }};
const receipt={itemId:"queued",disposition:"queued_followup",position:1,paused:true};
function fixture() {
 let state=initial;
 const submit=vi.fn(async(req:SurfaceActionRequest)=>{state={...state,action:req.action,status:"accepted",canWithdraw:true,itemId:"queued"};return receipt});
 const cancel=vi.fn(async()=>{state={...state,status:"withdrawn"}});
 return {port:{surfaceActionState:vi.fn(async()=>state),surfaceAction:submit,cancelQueued:cancel},submit,cancel,restore:(next:SurfaceActionRecord)=>{state=next}};
}
it("keeps refused drafts, retries with one identity, and restores readonly submission after reload",async()=>{
 const f=fixture();f.submit.mockRejectedValueOnce(new Error("refused"));
 const view=render(<SurfaceActionCard spec={spec} callId="call" port={f.port}/>);
 const input=await screen.findByLabelText("Draft text") as HTMLTextAreaElement;
 fireEvent.change(input,{target:{value:"  @file /command\n"}});
 fireEvent.click(screen.getByRole("button",{name:"Continue"}));
 await screen.findByRole("alert");
 expect(input.value).toBe("  @file /command\n");
 fireEvent.click(screen.getByRole("button",{name:"Continue"}));
 await waitFor(()=>expect(f.submit).toHaveBeenCalledTimes(2));
 expect(f.submit.mock.calls[0][0].action.requestId).toBe(f.submit.mock.calls[1][0].action.requestId);
 expect(f.submit.mock.calls[1][0].action.values).toEqual({draft:"  @file /command\n"});
 await waitFor(()=>expect(input.readOnly).toBe(true));
 view.unmount();
 render(<SurfaceActionCard spec={spec} callId="call" port={f.port}/>);
 const restored=await screen.findByLabelText("Draft text") as HTMLTextAreaElement;
 expect(restored.value).toBe("  @file /command\n");expect(restored.readOnly).toBe(true);
 expect(f.submit).toHaveBeenCalledTimes(2);
});
it("withdraws a queued action before editing and explicitly selects steer delivery",async()=>{
 const f=fixture();f.restore({...initial,itemId:"queued",status:"accepted",canWithdraw:true,action:{...initial.action,actionId:"continue",requestId:"request",values:{draft:"saved"}}});
 render(<SurfaceActionCard spec={spec} callId="call" port={f.port}/>);
 fireEvent.click(await screen.findByRole("button",{name:"撤回"}));
 await waitFor(()=>expect((screen.getByLabelText("Draft text") as HTMLTextAreaElement).readOnly).toBe(false));
 fireEvent.click(screen.getByRole("button",{name:"插入当前任务"}));
 fireEvent.click(screen.getByRole("button",{name:"Continue"}));
 await waitFor(()=>expect(f.submit).toHaveBeenCalledTimes(1));
 expect(f.submit.mock.calls[0][0].delivery).toBe("steer");expect(f.cancel).toHaveBeenCalledWith("queued");
});
it("reconciles a lost acknowledgment without enqueueing again",async()=>{
 const f=fixture();f.submit.mockImplementationOnce(async req=>{f.restore({...initial,action:req.action,itemId:"queued",status:"accepted",canWithdraw:true});throw new Error("response lost")});
 render(<SurfaceActionCard spec={spec} callId="call" port={f.port}/>);
 const input=await screen.findByLabelText("Draft text") as HTMLTextAreaElement;
 fireEvent.click(screen.getByRole("button",{name:"Continue"}));
 await waitFor(()=>expect(input.readOnly).toBe(true));
 expect(screen.queryByRole("alert")).toBeNull();expect(f.submit).toHaveBeenCalledTimes(1);
});
it("keeps a cold withdrawn draft readonly without reopening delivery controls",async()=>{
 const f=fixture();f.restore({...initial,active:false,status:"withdrawn",action:{...initial.action,values:{draft:"saved"}}});
 render(<SurfaceActionCard spec={spec} callId="call" port={f.port}/>);
 const input=await screen.findByLabelText("Draft text") as HTMLTextAreaElement;
 expect(input.value).toBe("saved");expect(input.readOnly).toBe(true);
 expect(screen.queryByRole("button",{name:"插入当前任务"})).toBeNull();expect(f.submit).not.toHaveBeenCalled();
});

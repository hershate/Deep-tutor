// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { watchTree } from "./tree_events";
import { RemoteEventSource } from "./cloud_remote";

class Source {
 static all: Source[]=[];
 onmessage: ((e:{data:string})=>void)|null=null;
 onopen: (()=>void)|null=null;
 close=vi.fn();
 constructor(readonly url:string){Source.all.push(this);}
 send(frame:unknown){this.onmessage?.({data:JSON.stringify(frame)});}
}
afterEach(()=>{Source.all=[];vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers();});

it("refreshes on attach, changes and a recovered gap, while unchanged watermarks cost no reads",()=>{
 vi.stubGlobal("EventSource",Source);
 const refresh=vi.fn();const stop=watchTree(refresh);const source=Source.all[0];
 expect(source.url).toBe("/tree/events");
 source.send({kind:"stream_watermark",seq:4});
 expect(refresh).toHaveBeenCalledTimes(1);
 source.send({kind:"stream_watermark",seq:4});
 source.send({kind:"stream_watermark",seq:4});
 expect(refresh).toHaveBeenCalledTimes(1);
 source.send({kind:"notice",code:"session_tree_changed",seq:5});
 source.send({kind:"stream_watermark",seq:5});
 expect(refresh).toHaveBeenCalledTimes(2);
 source.send({kind:"stream_gap",seq:8});
 source.send({kind:"stream_watermark",seq:0});
 expect(refresh).toHaveBeenCalledTimes(4);
 source.send({kind:"notice",code:"unrelated",seq:9});
 expect(refresh).toHaveBeenCalledTimes(4);
 stop();expect(source.close).toHaveBeenCalledOnce();
});

it("resumes a silent connection from the last invalidation cursor",()=>{
 vi.useFakeTimers();vi.stubGlobal("EventSource",Source);
 const refresh=vi.fn();const stop=watchTree(refresh);
 Source.all[0].send({kind:"notice",code:"session_tree_changed",seq:12});
 vi.advanceTimersByTime(45000);
 expect(Source.all[0].close).toHaveBeenCalledOnce();
 expect(Source.all[1].url).toBe("/tree/events?lastEventId=12");
 Source.all[1].send({kind:"stream_watermark",seq:12});
 expect(refresh).toHaveBeenCalledTimes(1);
 stop();
});

it("uses the existing remote replay transport without reading the tree on unchanged watermarks", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", RemoteEventSource);
  const fetch = vi.spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify({ frames: [], complete: true, watermark: 5 })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ frames: [], complete: true, watermark: 4 })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ frames: [{ kind: "notice", code: "session_tree_changed", seq: 5 }], complete: true, watermark: 5 })));
  const refresh = vi.fn();
  const stop = watchTree(refresh);
  await vi.advanceTimersByTimeAsync(0);
  expect(fetch.mock.calls[0][0]).toBe(`/tree/events/replay?lastEventId=${Number.MAX_SAFE_INTEGER}`);
  expect(refresh).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1500);
  expect(fetch.mock.calls[1][0]).toBe("/tree/events/replay?lastEventId=4");
  expect(refresh).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1500);
  expect(refresh).toHaveBeenCalledTimes(2);
  stop();
});

import { expect, it } from "vitest";
import { fromHistory, reduce, initialState as emptyState } from "./session";
import type { HistoryMessage } from "../port/port";

const spec = { spec_version: 1, title: "Count", body: [{ kind: "field", key: "count", label: "Count", field: { type: "number", required: true } }] };
const call = { id: "call-1", name: "use_capability", resolvedName: "render_surface", arguments: JSON.stringify({ action: "call", capability_id: "tool:render_surface", arguments: spec }) };

it("restores edited drafts verbatim with their preview and no entrance motion", () => {
  const draft = "  edited\n\n@file /command <script>\t ";
  const surface = { spec_version: 1, body: [{ kind: "editable_text", key: "draft", label: "Draft", field: { type: "string", required: true } }, { kind: "diff_preview", diff: { before: "old\n", after: "new\n" } }] };
  const history = fromHistory([{ role: "assistant", content: "", msgIndex: 0, toolCalls: [{ ...call, arguments: JSON.stringify({ action: "call", capability_id: "tool:render_surface", arguments: surface }) }] }, { role: "tool", content: JSON.stringify({ surface_id: "surface-1", values: { draft } }), msgIndex: 1, toolCallId: "call-1" }]);
  expect(history.items[0]).toMatchObject({ t: "ask", surfaceValues: { draft }, answered: [[]] });
  expect(reduce(emptyState, { kind: "__restore", ...history }).entranceOwed).toEqual([]);
});

it("restores typed answers as settled fields and leaves missing answers explicitly expired", () => {
  const messages: HistoryMessage[] = [{ role: "assistant", content: "", msgIndex: 0, toolCalls: [call] }];
  const missing = fromHistory(messages);
  expect(missing.items[0]).toMatchObject({ t: "ask", surfaceExpired: true, answered: [] });
  const done = fromHistory([...messages, { role: "tool", content: '{"surface_id":"surface-1","values":{"count":2}}', msgIndex: 1, toolCallId: "call-1" }]);
  expect(done.items[0]).toMatchObject({ t: "ask", surfaceValues: { count: 2 }, answered: [[]] });
  expect(reduce(emptyState, { kind: "__restore", ...done }).entranceOwed).toEqual([]);
});

it("refuses forged recorded values and preserves a live prompt when history refreshes", () => {
  const history = fromHistory([{ role: "assistant", content: "", msgIndex: 0, toolCalls: [call] }, { role: "tool", content: '{"surface_id":"surface-1","values":{"count":"2"}}', msgIndex: 1, toolCallId: "call-1" }]);
  expect(history.items[0]).toMatchObject({ t: "ask", surfaceExpired: true });
  const ask = { id: "ask-live", questions: [{ id: "surface", callId: "call-1", prompt: "Count", options: [], surface: spec }] };
  const state = reduce(emptyState, { kind: "ask_request", ask } as never);
  const restored = reduce(state, { kind: "__restore", ...history });
  expect(restored.items.filter(i => i.t === "ask" && i.answered === undefined)).toHaveLength(1);
  expect(restored.items.filter(i => i.t === "ask")).toHaveLength(1);
  const replayed = reduce(reduce(emptyState, { kind: "__restore", ...history }), { kind:"ask_request", ask } as never);
  expect(replayed.items.filter(i => i.t === "ask")).toHaveLength(1);
  expect(replayed.entranceOwed).toEqual([]);
});

it("expires a saved call that was interrupted before resolved metadata was recorded", () => {
  const history=fromHistory([{role:"assistant",content:"",msgIndex:0,toolCalls:[{...call,resolvedName:undefined}]}]);
  expect(history.items[0]).toMatchObject({t:"ask",surfaceExpired:true,answered:[]});
});

it("does not infer optional recorded values from inherited properties", () => {
  const optional={spec_version:1,body:[{kind:"field",key:"constructor",label:"Name",field:{type:"string"}}]};
  const history=fromHistory([{role:"assistant",content:"",msgIndex:0,toolCalls:[{...call,arguments:JSON.stringify({action:"call",capability_id:"tool:render_surface",arguments:optional})}]},{role:"tool",content:'{"surface_id":"surface-1","values":{}}',msgIndex:1,toolCallId:"call-1"}]);
  expect(history.items[0]).toMatchObject({t:"ask",surfaceValues:{},answered:[[]]});
});

it("expires cancelled surfaces and respects a different actual resolved target", () => {
  const cancelled=fromHistory([{role:"assistant",content:"",msgIndex:0,toolCalls:[call]},{role:"tool",content:"cancelled",msgIndex:1,toolCallId:"call-1",toolFailed:true}]);
  expect(cancelled.items[0]).toMatchObject({t:"ask",surfaceExpired:true});
  const other=fromHistory([{role:"assistant",content:"",msgIndex:0,toolCalls:[{...call,resolvedName:"other_tool"}]}]);
  expect(other.items[0]).toMatchObject({t:"tool"});
});

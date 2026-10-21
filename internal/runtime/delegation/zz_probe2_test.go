package delegation

import (
	"context"
	"deep-tutor/internal/runtime/agent"
	"deep-tutor/internal/runtime/writeclaim"
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/contract/provider"
	"deep-tutor/internal/contract/tool"
)

func TestZZProbeThroughTaskTool(t *testing.T) {
	root := testenv.TempDir(t)
	probe := &auditProbe2{Sink: event.Discard}
	reg := tool.NewRegistry()
	reg.Add(fakeReadFileTool{})
	prov := &scriptedProvider{name: "p", turns: [][]provider.Chunk{
		{toolCallChunk("1", "read_file", `{"path":"a.go"}`), {Type: provider.ChunkDone}},
		{{Type: provider.ChunkText, Text: "done"}, {Type: provider.ChunkDone}},
	}}
	task := NewTaskTool(prov, nil, reg, 20, 0, 0, 0, 0.0, "", "sys", nil, 0, "", "", nil).
		WithTranscripts(mustSubagentStore(t), root, "base", "high").
		WithScheduler(writeclaim.NewSubagentScheduler(4, 4))
	ctx := agent.WithCallContext(context.Background(), "call-1", probe, nil, false)
	if _, err := task.Execute(ctx, []byte(`{"prompt":"inspect a.go"}`)); err != nil {
		t.Fatalf("task: %v", err)
	}
	t.Logf("audits через TaskTool: %d", len(probe.got))
	if len(probe.got) == 0 {
		t.Fatal("no audit through the TaskTool path")
	}
}

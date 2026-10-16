package agent

import (
	"context"
	"encoding/json"
	"sync"
	"sync/atomic"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/agent/testutil"
	"reasonix/internal/state/sessionstore"
)

func TestOnlyTheTopLevelRunReportsItsTime(t *testing.T) {
	var reports atomic.Int64
	ctx := WithRunTimeSink(context.Background(), func(int64) { reports.Add(1) })

	top := New(testutil.NewMock("top", testutil.Turn{Text: "done"}), tool.NewRegistry(), sessionstore.NewSession("s"), Options{}, event.Discard)
	if err := top.Run(ctx, "go"); err != nil {
		t.Fatal(err)
	}
	if reports.Load() != 1 {
		t.Fatalf("the top-level run reported %d times, want 1", reports.Load())
	}

	var wg sync.WaitGroup
	for range 4 {
		wg.Go(func() {
			prov := testutil.NewMock("sub", testutil.Turn{Text: "child answer"})
			if _, err := RunSubAgentWithSession(ctx, prov, tool.NewRegistry(), sessionstore.NewSession("s"), "task", Options{}, event.Discard); err != nil {
				t.Error(err)
			}
		})
	}
	wg.Wait()
	if reports.Load() != 1 {
		t.Fatalf("four parallel sub-agents moved the turn's reports to %d, want 1", reports.Load())
	}
}

type nestedRunTool struct{ prov *testutil.MockProvider }

func (*nestedRunTool) Name() string        { return "nested" }
func (*nestedRunTool) Description() string { return "runs a model of its own, as a reviewer does" }
func (*nestedRunTool) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{}}`)
}
func (*nestedRunTool) ReadOnly() bool { return true }
func (n *nestedRunTool) Execute(ctx context.Context, _ json.RawMessage) (string, error) {
	nested := New(n.prov, tool.NewRegistry(), sessionstore.NewSession("s"), Options{}, event.Discard)
	return "reviewed", nested.Run(ctx, "review")
}

func TestARunInsideATurnsToolDoesNotReportItsTime(t *testing.T) {
	var reports atomic.Int64
	ctx := WithRunTimeSink(context.Background(), func(int64) { reports.Add(1) })
	reg := tool.NewRegistry()
	reg.Add(&nestedRunTool{prov: testutil.NewMock("reviewer", testutil.Turn{Text: "verdict"})})
	top := New(testutil.NewMock("top",
		testutil.Turn{ToolCalls: []provider.ToolCall{{ID: "c1", Name: "nested", Arguments: `{}`}}},
		testutil.Turn{Text: "done"},
	), reg, sessionstore.NewSession("s"), Options{}, event.Discard)
	if err := top.Run(ctx, "go"); err != nil {
		t.Fatal(err)
	}
	if reports.Load() != 1 {
		t.Fatalf("the turn got %d reports, want only its own", reports.Load())
	}
}

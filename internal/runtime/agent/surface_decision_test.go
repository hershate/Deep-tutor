package agent

import (
	"context"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/usecap"
	"testing"
)

func TestSurfaceDecisionCutsDeferredCallBatchBeforeAnswer(t *testing.T) {
	reg := tool.NewRegistry()
	reg.Add(NewRenderSurfaceTool())
	reg.Add(NewAskTool())
	reg.Add(usecap.NewUseCapabilityTool(context.Background(), nil, nil, reg, nil, nil, nil))
	for _, args := range []string{`{"action":"call","capability_id":"tool:render_surface"}`, `{"action":"call","capability_id":"tool:ask"}`} {
		calls := []provider.ToolCall{{Name: "use_capability", Arguments: args}, {Name: "ask"}}
		scheduled, barrier := scheduleUpToDecision(reg, calls)
		if len(scheduled) != 1 || barrier != 0 {
			t.Fatalf("scheduled=%d barrier=%d", len(scheduled), barrier)
		}
	}
	calls := []provider.ToolCall{{Name: "use_capability", Arguments: `{"action":"inspect","capability_id":"tool:render_surface"}`}, {Name: "ask"}}
	if scheduled, barrier := scheduleUpToDecision(reg, calls); len(scheduled) != 2 || barrier != 1 {
		t.Fatal("inspect became a decision")
	}
}

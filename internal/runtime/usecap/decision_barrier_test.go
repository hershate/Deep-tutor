package usecap

import (
	"context"
	"encoding/json"
	"reasonix/internal/contract/tool"
	"testing"
)

type decisionTestTool struct{}

func (decisionTestTool) Name() string            { return "decision_test" }
func (decisionTestTool) Description() string     { return "Test user decision" }
func (decisionTestTool) ReadOnly() bool          { return true }
func (decisionTestTool) Schema() json.RawMessage { return json.RawMessage(`{"type":"object"}`) }
func (decisionTestTool) Execute(context.Context, json.RawMessage) (string, error) {
	panic("barrier lookup executed tool")
}
func (decisionTestTool) DecisionBarrier() bool { return true }

func TestDeferredDecisionBarrierIsStructuralAndDoesNotExecute(t *testing.T) {
	reg := tool.NewRegistry()
	reg.Add(decisionTestTool{})
	proxy := &UseCapabilityTool{registry: reg}
	for _, test := range []struct {
		args string
		want bool
	}{
		{`{"action":"call","capability_id":"tool:decision_test"}`, true},
		{`{"action":"inspect","capability_id":"tool:decision_test"}`, false},
		{`{"action":"search","query":"tool:decision_test"}`, false},
		{`{"action":"call","capability_id":"tool:unknown"}`, false},
		{`{"action":"call","capability_id":"mcp-tool:server/decision_test"}`, false},
	} {
		if got := tool.IsDecisionBarrierCall(proxy, json.RawMessage(test.args)); got != test.want {
			t.Fatalf("%s: %v", test.args, got)
		}
	}
}

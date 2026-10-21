package agent

import (
	"encoding/json"

	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/contract/planmode"
	"deep-tutor/internal/contract/provider"
	"deep-tutor/internal/contract/tool"
)

// These expose the agent's own judgements to the external tests in this
// directory, which check them against the real delegation tools.

func BatchCallStaticallySkippable(reg *tool.Registry, call provider.ToolCall) bool {
	return batchCallStaticallySkippable(&Agent{svc: agentServices{sink: event.Discard, tools: reg}}, call)
}

func DelegationProfile(t tool.Tool, args json.RawMessage) *event.Profile {
	return delegationProfile(t, args)
}

func PlanModeDecision(a *Agent, t tool.Tool, safety planmode.PlanSafety) planmode.Decision {
	return a.planModeDecision(t, t.Name(), t.ReadOnly(), safety, nil)
}

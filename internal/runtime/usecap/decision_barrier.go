package usecap

import (
	"encoding/json"
	"reasonix/internal/contract/tool"
	"strings"
)

func (t *UseCapabilityTool) DecisionBarrierCall(args json.RawMessage) bool {
	var p struct {
		Action string `json:"action"`
		ID     string `json:"capability_id"`
	}
	if json.Unmarshal(args, &p) != nil || !strings.EqualFold(strings.TrimSpace(p.Action), "call") || t.registry == nil {
		return false
	}
	name, ok := strings.CutPrefix(strings.TrimSpace(p.ID), "tool:")
	if !ok {
		return false
	}
	target, ok := t.registry.Get(strings.TrimSpace(name))
	return ok && tool.IsDecisionBarrier(target)
}

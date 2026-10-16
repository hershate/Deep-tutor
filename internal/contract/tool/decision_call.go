package tool

import "encoding/json"

type DecisionCallBarrier interface {
	DecisionBarrierCall(args json.RawMessage) bool
}

func IsDecisionBarrierCall(t Tool, args json.RawMessage) bool {
	if IsDecisionBarrier(t) {
		return true
	}
	b, ok := t.(DecisionCallBarrier)
	return ok && b.DecisionBarrierCall(args)
}

package surfacespec

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
)

func (s *Spec) Actions() []Node {
	var actions []Node
	var walk func([]Node)
	walk = func(nodes []Node) {
		for _, node := range nodes {
			if node.Kind == "button" {
				actions = append(actions, node)
			}
			walk(node.Children)
		}
	}
	walk(s.Body)
	return actions
}

func (s *Spec) ActionValues(id string, raw []byte) (map[string]json.RawMessage, error) {
	for _, action := range s.Actions() {
		if action.ActionID == id {
			return s.ParseAnswers(raw)
		}
	}
	return nil, ErrValueInvalid
}

func (s *Spec) validateActions() error {
	seen := map[string]bool{}
	for _, action := range s.Actions() {
		if seen[action.ActionID] {
			return fail(CodeSchemaInvalid, "/actionId")
		}
		seen[action.ActionID] = true
	}
	if len(seen) > MaxOptions {
		return fail(CodeLimitExceeded, "/actionId")
	}
	return nil
}

func (s *Spec) ID() string {
	raw, _ := s.Canonical()
	return fmt.Sprintf("surface-%x", sha256.Sum256(raw))[:24]
}

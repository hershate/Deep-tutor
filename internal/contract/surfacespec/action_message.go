package surfacespec

import (
	"encoding/json"
	"strings"
)

const ActionMessageName = "surface_action"

type Action struct {
	SurfaceID string          `json:"surfaceId"`
	SessionID string          `json:"sessionId"`
	CallID    string          `json:"callId"`
	ActionID  string          `json:"actionId"`
	RequestID string          `json:"requestId"`
	Values    json.RawMessage `json:"values"`
}

func (a Action) ValidateIdentity() error {
	if !identifier(a.SurfaceID) || a.SessionID == "" || len(a.SessionID) > 200 || a.CallID == "" || len(a.CallID) > 200 || !identifier(a.ActionID) || !identifier(a.RequestID) {
		return ErrValueInvalid
	}
	return nil
}

func (a Action) Note() (string, error) {
	if err := a.ValidateIdentity(); err != nil {
		return "", err
	}
	raw, err := json.Marshal(a)
	if err != nil {
		return "", err
	}
	return "<surface_action>\n" + string(raw) + "\n</surface_action>", nil
}

func FromCall(name, resolved, args string) (*Spec, error) {
	if resolved != "" && resolved != ToolName {
		return nil, ErrExpired
	}
	raw := []byte(args)
	if name != ToolName {
		if name != "use_capability" {
			return nil, ErrExpired
		}
		var proxy struct {
			Action    string          `json:"action"`
			ID        string          `json:"capability_id"`
			Arguments json.RawMessage `json:"arguments"`
		}
		if json.Unmarshal(raw, &proxy) != nil || !strings.EqualFold(strings.TrimSpace(proxy.Action), "call") || strings.TrimSpace(proxy.ID) != "tool:render_surface" {
			return nil, ErrExpired
		}
		raw = proxy.Arguments
	}
	return Parse(raw)
}

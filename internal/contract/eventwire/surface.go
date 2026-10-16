package eventwire

import (
	"encoding/json"
	"reasonix/internal/base/secrets"
	"reasonix/internal/contract/surfacespec"
	"strings"
)

func SurfaceArgs(name, resolved, args string) string {
	if resolved != "" && resolved != surfacespec.ToolName {
		return args
	}
	if name != surfacespec.ToolName && resolved != surfacespec.ToolName {
		var p struct {
			Action string `json:"action"`
			ID     string `json:"capability_id"`
		}
		if name != "use_capability" || json.Unmarshal([]byte(args), &p) != nil || !strings.EqualFold(strings.TrimSpace(p.Action), "call") || strings.TrimSpace(p.ID) != "tool:render_surface" {
			return args
		}
	}
	raw := []byte(args)
	var proxy map[string]json.RawMessage
	if name != surfacespec.ToolName {
		if json.Unmarshal(raw, &proxy) != nil {
			return ""
		}
		raw = proxy["arguments"]
	}
	s, err := surfacespec.Parse(raw)
	if err != nil {
		return ""
	}
	s.Redact(secrets.RedactCredentials)
	if s.Validate() != nil {
		return ""
	}
	canonical, err := s.Canonical()
	if err != nil {
		return ""
	}
	if proxy != nil {
		proxy["arguments"] = canonical
		canonical, err = json.Marshal(proxy)
	}
	if err != nil {
		return ""
	}
	return string(canonical)
}

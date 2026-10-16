package computer

import (
	"context"
	"encoding/json"
	"maps"
)

type nativeToolSchema struct {
	Type       string                     `json:"type"`
	Properties map[string]json.RawMessage `json:"properties"`
}

func (h *Driver) loadToolContract(ctx context.Context) error {
	var catalog struct {
		Tools []struct {
			Name   string           `json:"name"`
			Schema nativeToolSchema `json:"inputSchema"`
		} `json:"tools"`
	}
	if err := h.rpc(ctx, "tools/list", map[string]any{}, &catalog); err != nil {
		return err
	}
	tools := map[string]nativeToolSchema{}
	for _, tool := range catalog.Tools {
		if !driverTools[tool.Name] {
			continue
		}
		if tool.Schema.Type != "object" {
			return fail(CodeUnsupported, "CUA native tool has no object argument contract")
		}
		if _, exists := tools[tool.Name]; exists {
			return fail(CodeFailed, "duplicate CUA native tool contract")
		}
		tools[tool.Name] = tool.Schema
	}
	if len(tools) == 0 {
		return fail(CodeUnsupported, "CUA advertised no supported native tool contracts")
	}
	h.live.tools = tools
	return nil
}

func (p *driverProcess) arguments(name string, args map[string]any) (map[string]any, error) {
	schema, ok := p.tools[name]
	if !ok {
		return nil, fail(CodeUnsupported, "CUA does not advertise native tool %s", name)
	}
	if mode, present := args["delivery_mode"]; present {
		if _, supported := schema.Properties["delivery_mode"]; !supported {
			// Without delivery_mode, the pinned set_value contract is background-only.
			if name != "set_value" || mode != "background" {
				return nil, fail(CodeUnsupported, "CUA %s has no compatible delivery-mode contract", name)
			}
			args = maps.Clone(args)
			delete(args, "delivery_mode")
		}
	}
	return args, nil
}

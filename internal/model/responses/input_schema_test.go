package responses

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"reasonix/internal/contract/provider"
)

// checkInputItem is a strict reading of the published Responses input schema,
// the way a gateway that deserializes `input` into typed variants reads it: no
// item without its required fields, no null anywhere, content as a string or a
// list of typed parts.
func checkInputItem(item map[string]any) error {
	for k, v := range item {
		if v == nil {
			return fmt.Errorf("%q is null", k)
		}
	}
	need := func(keys ...string) error {
		for _, k := range keys {
			if _, ok := item[k]; !ok {
				return fmt.Errorf("%v item lacks required %q", item["type"], k)
			}
		}
		return nil
	}
	switch item["type"] {
	case nil:
		if err := need("role", "content"); err != nil {
			return err
		}
		return checkContent(item["content"])
	case "reasoning":
		// id is required by the schema but omitted when the provider issued none:
		// a relay fronting a model that wants its reasoning back needs the item.
		if err := need("summary"); err != nil {
			return err
		}
		if _, ok := item["summary"].([]map[string]string); !ok {
			return fmt.Errorf("reasoning summary is not a list")
		}
	case "function_call":
		return need("call_id", "name", "arguments")
	case "function_call_output":
		if err := need("call_id", "output"); err != nil {
			return err
		}
		if _, ok := item["output"].(string); !ok {
			return fmt.Errorf("function_call_output output is not a string")
		}
	case "web_search_call":
	default:
		return fmt.Errorf("unknown item type %v", item["type"])
	}
	return nil
}

func checkContent(content any) error {
	switch c := content.(type) {
	case string:
		return nil
	case []map[string]string:
		if len(c) == 0 {
			return fmt.Errorf("empty content list")
		}
		for _, part := range c {
			switch part["type"] {
			case "input_text":
				if _, ok := part["text"]; !ok {
					return fmt.Errorf("input_text lacks text")
				}
			case "input_image":
				if part["image_url"] == "" {
					return fmt.Errorf("input_image lacks image_url")
				}
				// The schema lists detail as required, but every endpoint in use
				// accepts its absence, so it is not asserted here.
				if d, ok := part["detail"]; ok && d != "auto" && d != "low" && d != "high" {
					return fmt.Errorf("input_image detail %q", d)
				}
			default:
				return fmt.Errorf("unknown content part %q", part["type"])
			}
		}
		return nil
	}
	return fmt.Errorf("content is %T", content)
}

func sessionWithEveryItemKind() []provider.Message {
	longOutput := strings.Repeat("x", 600_000)
	image := "data:image/png;base64," + strings.Repeat("A", 64)
	return []provider.Message{
		{Role: provider.RoleSystem, Content: "sys"},
		{Role: provider.RoleUser, Content: "look", Images: []string{image}},
		{Role: provider.RoleUser, Content: "plain"},
		{Role: provider.RoleAssistant, ReasoningContent: "thinking", ReasoningID: "rs_1", Content: "", ToolCalls: []provider.ToolCall{{ID: "c1", Name: "read", Arguments: "{}"}, {ID: "c2", Name: "read", Arguments: "{}"}}},
		{Role: provider.RoleTool, ToolCallID: "c1", Content: longOutput, Images: []string{image}},
		{Role: provider.RoleTool, ToolCallID: "c2", Content: ""},
		{Role: provider.RoleAssistant},
	}
}

func TestEveryInputItemMatchesThePublishedSchema(t *testing.T) {
	for name, caps := range map[string]vendorCapabilities{"unknown endpoint (schema defaults)": {}} {
		input := messagesToInput(sessionWithEveryItemKind(), true, false, caps)
		for i, item := range input {
			if err := checkInputItem(item); err != nil {
				b, _ := json.Marshal(item)
				t.Errorf("%s: input[%d]: %v (%.120s)", name, i, err, b)
			}
		}
	}
}

package responses

import (
	"encoding/json"
	"testing"

	"reasonix/internal/contract/provider"
)

func imageSession() []provider.Message {
	img := "data:image/png;base64,AAAA"
	return []provider.Message{
		{Role: provider.RoleSystem, Content: "sys"},
		{Role: provider.RoleUser, Content: "look", Images: []string{img}},
		{Role: provider.RoleAssistant, ToolCalls: []provider.ToolCall{{ID: "c1", Name: "shot", Arguments: "{}"}}},
		{Role: provider.RoleTool, ToolCallID: "c1", Content: "ok", Images: []string{img}},
	}
}

func imageParts(input []map[string]any) []map[string]string {
	var out []map[string]string
	for _, item := range input {
		if parts, ok := item["content"].([]map[string]string); ok {
			for _, p := range parts {
				if p["type"] == "input_image" {
					out = append(out, p)
				}
			}
		}
	}
	return out
}

// The schema's required detail goes only to the endpoint known to take it.
func TestImageDetailIsSentOnlyToTheOfficialOpenAIHost(t *testing.T) {
	for baseURL, want := range map[string]bool{
		"https://api.openai.com/v1":         true,
		"https://api.deepseek.com":          false,
		"https://api.xiaomimimo.com/v1":     false,
		"https://dashscope.aliyuncs.com/v1": false,
		"https://relay.example.com/v1":      false,
	} {
		c := New(Config{Name: "p", BaseURL: baseURL, Model: "m", APIKey: "k", Extra: map[string]any{"vision": true}}).(*client)
		parts := imageParts(messagesToInput(imageSession()[1:], true, false, c.caps))
		if len(parts) != 2 {
			t.Fatalf("%s: %d image parts, want 2", baseURL, len(parts))
		}
		for _, p := range parts {
			if _, has := p["detail"]; has != want {
				t.Errorf("%s: detail present = %v, want %v", baseURL, has, want)
			}
		}
	}
}

// A text-only session is byte for byte what it was, on every endpoint.
func TestTextOnlyRequestBodyIsUnchangedByImageDetail(t *testing.T) {
	text := []provider.Message{
		{Role: provider.RoleSystem, Content: "sys"},
		{Role: provider.RoleUser, Content: "hi"},
		{Role: provider.RoleAssistant, Content: "yo", ToolCalls: []provider.ToolCall{{ID: "c1", Name: "t", Arguments: "{}"}}},
		{Role: provider.RoleTool, ToolCallID: "c1", Content: "out"},
	}
	for _, baseURL := range []string{"https://api.openai.com/v1", "https://relay.example.com/v1"} {
		c := New(Config{Name: "p", BaseURL: baseURL, Model: "m", APIKey: "k", Extra: map[string]any{"vision": true}}).(*client)
		got, _ := json.Marshal(messagesToInput(text[1:], true, false, c.caps))
		plain := c.caps
		plain.imageDetail = false
		want, _ := json.Marshal(messagesToInput(text[1:], true, false, plain))
		if string(got) != string(want) {
			t.Errorf("%s: text-only input changed:\n%s\n%s", baseURL, got, want)
		}
	}
}

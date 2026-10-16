package eventwire

import (
	"encoding/json"
	"reasonix/internal/base/secrets"
	"reasonix/internal/contract/surfacespec"
	"strings"
	"testing"
)

func TestDraftDiffWireAndHistoryRedaction(t *testing.T) {
	credential := "sk-" + strings.Repeat("x", 48)
	spec := surfacespec.Spec{SpecVersion: 1, Body: []surfacespec.Node{
		{Kind: "editable_text", Key: "draft", Label: "Draft", Field: &surfacespec.Field{Type: "string"}},
		{Kind: "diff_preview", Diff: &surfacespec.Diff{Before: credential, After: "updated " + credential}},
	}}
	wire, err := json.Marshal(viewNodes(spec.Body))
	if err != nil || !strings.Contains(string(wire), `"before"`) || !strings.Contains(string(wire), `"after"`) {
		t.Fatalf("diff missing from wire: %s %v", wire, err)
	}
	raw, _ := spec.Canonical()
	got := SurfaceArgs("render_surface", "", string(raw))
	if got == "" || strings.Contains(got, credential) {
		t.Fatalf("history redaction: %s", got)
	}
}

func TestSurfaceArgsRedactionDoesNotChangeNeighbouringTools(t *testing.T) {
	credential := "sk-" + strings.Repeat("x", 48)
	if secrets.RedactCredentials(credential) == credential {
		t.Fatal("fixture is not a recognised credential")
	}
	raw, _ := json.Marshal(map[string]any{"spec_version": 1, "body": []any{map[string]any{"kind": "field", "key": "value", "label": credential, "field": map[string]any{"type": "string", "default": credential}}}})
	got := SurfaceArgs("render_surface", "", string(raw))
	if got == "" || strings.Contains(got, credential) {
		t.Fatalf("redaction: %s", got)
	}
	if got := SurfaceArgs("ask", "", string(raw)); got != string(raw) {
		t.Fatal("ordinary ask changed")
	}
	proxy, _ := json.Marshal(map[string]any{"action": "call", "capability_id": "tool:render_surface", "arguments": json.RawMessage(raw)})
	if got := SurfaceArgs("use_capability", "render_surface", string(proxy)); got == "" || strings.Contains(got, credential) {
		t.Fatal(got)
	}
	if got := SurfaceArgs("use_capability", "", string(proxy)); got == "" || strings.Contains(got, credential) {
		t.Fatal("interrupted/eager arguments were not redacted")
	}
	if got := SurfaceArgs("use_capability", "other_tool", string(proxy)); got != string(proxy) {
		t.Fatal("actual resolved target was ignored")
	}
}

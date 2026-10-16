package agent

import (
	"encoding/json"
	"testing"

	"reasonix/internal/contract/tool"
	"reasonix/internal/ext/plugin"
	"reasonix/internal/runtime/usecap"
)

func TestMCPAllowlistRequiresKnownIdentity(t *testing.T) {
	parent := tool.NewRegistry()
	host := plugin.NewHost()
	defer host.Close()
	spec := plugin.Spec{Name: "my.server", StripRawPrefix: "vendor_"}
	cache := &plugin.CachedSchema{Tools: []plugin.CachedTool{{Name: "vendor_search.issues", Schema: json.RawMessage(`{"type":"object"}`)}}}
	adapters := plugin.LazyToolset(spec, cache, host, parent, t.Context(), false)
	parent.Add(adapters[0])
	parent.Add(fakeTool{name: "mcp__legacy__search"})
	parent.Add(annotatedMCPTool{fakeTool: fakeTool{name: "mcp__incomplete__search"}, server: "incomplete"})
	parent.Add(usecap.NewUseCapabilityTool(t.Context(), nil, nil, parent, nil, nil, nil))
	for _, tc := range []struct {
		name string
		want string
	}{
		{adapters[0].Name(), "mcp-tool:my.server/vendor_search.issues"},
		{plugin.ToolPrefix(spec.Name) + "*", "mcp-tool:my.server/vendor_search.issues"},
		{"mcp-tool:offline/search.issues", "mcp-tool:offline/search.issues"},
		{"mcp-server:offline", "mcp-server:offline"},
		{"mcp__offline__search", ""},
		{"mcp__legacy__search", ""},
		{"mcp__incomplete__search", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			allowed := mcpCapabilityAllowlist(parent, []string{tc.name})
			if tc.want != "" {
				if len(allowed) != 1 || !allowed[tc.want] {
					t.Fatalf("allowlist=%v, want %q", allowed, tc.want)
				}
			} else if len(allowed) != 0 {
				t.Fatalf("unknown identity acquired a grant: %v", allowed)
			}
			child := SubagentToolRegistry(parent, []string{tc.name})
			_, found := child.Get("use_capability")
			if found != (tc.want != "") {
				t.Fatalf("proxy present=%v for identity %q", found, tc.want)
			}
		})
	}
	if got := mcpCapabilityAllowlist(nil, []string{"mcp__offline__search"}); len(got) != 0 {
		t.Fatalf("absent registry inferred a grant: %v", got)
	}
}

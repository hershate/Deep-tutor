package capability

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/contract/tool"
	"reasonix/internal/ext/plugin"
)

func TestCatalogPreservesMCPAdapterIdentity(t *testing.T) {
	for _, tc := range []struct{ server, raw, strip string }{
		{"github", "search", ""},
		{"my.server", "search.issues", ""},
		{"my__server", strings.Repeat("long_tool_", 20), ""},
		{"github", "vendor_search", "vendor_"},
	} {
		t.Run(tc.server+"/"+tc.raw, func(t *testing.T) {
			host := plugin.NewHost()
			t.Cleanup(host.Close)
			reg := tool.NewRegistry()
			spec := plugin.Spec{Name: tc.server, StripRawPrefix: tc.strip, Authorized: true}
			cached := &plugin.CachedSchema{Tools: []plugin.CachedTool{{Name: tc.raw, Schema: json.RawMessage(`{"type":"object"}`), ReadOnly: true}}}
			for _, adapter := range plugin.LazyToolset(spec, cached, host, reg, t.Context(), false) {
				reg.Add(adapter)
			}
			before := reg.Schemas()
			for _, snapshot := range [][]tool.ContractEntry{reg.ContractEntries(), reg.AllContractEntries()} {
				encoded, err := json.Marshal(snapshot)
				if err != nil {
					t.Fatal(err)
				}
				binding := snapshot[0].MCP
				snapshot[0].MCP = nil
				withoutMetadata, _ := json.Marshal(snapshot)
				if string(encoded) != string(withoutMetadata) {
					t.Fatal("host metadata changed serialized contract")
				}
				snapshot[0].MCP = binding
				for _, status := range []Status{StatusReady, StatusFailed, StatusDisabled} {
					cat := BuildCatalog(CatalogOptions{
						Tools: snapshot, Plugins: []config.PluginEntry{{Name: tc.server}},
						Connected: map[string]bool{tc.server: true},
						Failed:    map[string]string{tc.server: map[Status]string{StatusFailed: "offline"}[status]},
						Disabled:  map[string]bool{tc.server: status == StatusDisabled},
					})
					entry, ok := cat.Lookup("mcp-tool:" + tc.server + "/" + tc.raw)
					if !ok || entry.Source != tc.server || entry.Name != tc.server+"/"+tc.raw || entry.ToolName != reg.Names()[0] || entry.Status != status {
						t.Fatalf("raw identity/status lost: %+v", cat.Entries)
					}
					if len(cat.Entries) != 2 {
						t.Fatalf("unexpected duplicate capabilities: %+v", cat.Entries)
					}
				}
			}
			if !reflect.DeepEqual(before, reg.Schemas()) {
				t.Fatal("catalog read changed provider schemas")
			}
		})
	}
}

func TestCatalogDoesNotInferMCPIdentityFromCallableName(t *testing.T) {
	for _, name := range []string{"read_file", "mcp__github__search", plugin.ModelToolName("my.server", "a.b")} {
		entries := ToolEntries([]tool.ContractEntry{{Name: name}})
		if len(entries) != 1 || entries[0].ID != "tool:"+name || entries[0].Kind != KindTool {
			t.Fatalf("metadata-less tool %q acquired an MCP identity: %+v", name, entries)
		}
	}
}

func TestCatalogRequiresMatchingBindingToSuppressProxyTools(t *testing.T) {
	for _, contract := range []tool.ContractEntry{
		{Name: "mcp__my__unbound"},
		{Name: "mcp__my__server__read", MCP: &tool.MCPBinding{Server: "my__server", RawName: "read", CapabilityID: "mcp-tool:my__server/read"}},
	} {
		cat := BuildCatalog(CatalogOptions{
			Tools: []tool.ContractEntry{contract}, Plugins: []config.PluginEntry{{Name: "my"}},
			Connected: map[string]bool{"my": true}, ProxyTools: map[string][]plugin.CachedTool{"my": {{Name: "search"}}},
		})
		if entry, ok := cat.Lookup("mcp-tool:my/search"); !ok || entry.Status != StatusReady {
			t.Fatalf("unrelated callable name suppressed live proxy tools: %+v", cat.Entries)
		}
	}
}

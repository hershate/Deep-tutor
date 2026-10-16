package plugin

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"reasonix/internal/contract/tool"
)

func TestToolCatalogRejectsModelNameCollisions(t *testing.T) {
	redirectCache(t)
	long := strings.Repeat("x", 150)
	for _, tc := range []struct {
		name, strip string
		raw         []string
	}{
		{"normalized", "", []string{"a.b", normalizeName("a.b")}},
		{"shortened", "", []string{long, strings.TrimPrefix(toolName("srv", long), ToolPrefix("srv"))}},
		{"stripped", "srv_", []string{"srv_read", "read"}},
		{"duplicate", "", []string{"read", "read"}},
		{"empty", "", []string{" "}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			spec := Spec{Name: "srv", StripRawPrefix: tc.strip}
			var listed []mcpTool
			var cached []CachedTool
			for _, raw := range tc.raw {
				listed = append(listed, pinTool(raw, raw))
				cached = append(cached, CachedTool{Name: raw, Schema: listed[len(listed)-1].InputSchema})
			}
			body, err := json.Marshal(map[string]any{"tools": listed})
			if err != nil {
				t.Fatal(err)
			}
			c := &Client{name: spec.Name, spec: spec, t: &countingToolsTransport{raw: body}}
			tools, err := c.listTools(context.Background())
			if !errors.Is(err, ErrAmbiguousToolCatalog) || len(tools) != 0 || c.toolsListed {
				t.Errorf("colliding live catalog was accepted: tools=%v err=%v", toolNames(tools), err)
			}
			host := NewHost()
			defer host.Close()
			if err := SaveCachedSchema(spec.Name, CachedSchema{CacheKey: SchemaCacheKey(spec), Tools: cached}); err != nil {
				t.Fatal(err)
			}
			cs, ok := LoadCachedSchema(spec.Name, SchemaCacheKey(spec))
			if !ok {
				t.Fatal("schema cache did not survive reload")
			}
			if usable, ok := LoadCachedSchemaForSpec(spec); usable != nil || ok {
				t.Error("spec-aware loader accepted an unusable catalog")
			}
			tools = LazyToolset(spec, cs, host, tool.NewRegistry(), context.Background(), false)
			if len(tools) != 1 || tools[0].Name() != "mcp__srv__connect" || tools[0].(*lazyTool).hasCache {
				t.Errorf("colliding cache did not fall back to a handshake: %v", toolNames(tools))
			}
		})
	}
}

func TestToolCatalogPreservesDistinctNamesAndAliases(t *testing.T) {
	redirectCache(t)
	for _, disabled := range []bool{false, true} {
		spec := Spec{Name: "srv", StripRawPrefix: "srv_"}
		listed := []mcpTool{pinTool("srv_read", ""), pinTool("Read", ""), pinTool("a.b", ""), pinTool("a_b", ""), pinTool("connect", "")}
		if disabled {
			spec.DisabledTools = []string{"read"}
			listed = append(listed, pinTool("read", ""))
		}
		body, err := json.Marshal(map[string]any{"tools": listed})
		if err != nil {
			t.Fatal(err)
		}
		c := &Client{name: spec.Name, spec: spec, t: &countingToolsTransport{raw: body}}
		live, err := c.listTools(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		host := NewHost()
		defer host.Close()
		cached := cacheableToolsOf(live)
		if disabled {
			cached = append(cached, CachedTool{Name: "read", Schema: listed[0].InputSchema})
		}
		if err := SaveCachedSchema(spec.Name, CachedSchema{CacheKey: SchemaCacheKey(spec), Tools: cached}); err != nil {
			t.Fatal(err)
		}
		if loaded, ok := LoadCachedSchemaForSpec(spec); !ok || len(loaded.Tools) != 5 {
			t.Fatal("distinct enabled names were not a usable cache hit")
		}
		lazy := LazyToolset(spec, &CachedSchema{Tools: cached}, host, tool.NewRegistry(), context.Background(), false)
		for _, tools := range [][]tool.Tool{live, lazy} {
			reg := tool.NewRegistry()
			reg.Add(testTool{name: "read"})
			for _, adapter := range tools {
				reg.Add(adapter)
			}
			if len(reg.Schemas()) != 6 {
				t.Fatalf("distinct tools were lost: %v", toolNames(tools))
			}
			for ref, want := range map[string]string{
				"read": "read", "srv_read": "mcp__srv__read", "srv/read": "mcp__srv__read",
				"mcp-tool:srv/srv_read": "mcp__srv__read", "Read": "mcp__srv__Read",
				"a.b": toolName("srv", "a.b"), "a_b": "mcp__srv__a_b", "connect": "mcp__srv__connect",
			} {
				resolved, canonical, ambiguous := reg.ResolveCall(ref)
				if resolved == nil || canonical != want || len(ambiguous) != 0 {
					t.Errorf("ResolveCall(%q) = (%v, %q, %v), want %q", ref, resolved, canonical, ambiguous, want)
				}
			}
		}
	}
}

func TestHostRejectsCollidingCatalogAndRecovers(t *testing.T) {
	redirectCache(t)
	var collision atomic.Bool
	collision.Store(true)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			ID     *int   `json:"id"`
			Method string `json:"method"`
			Params struct {
				Name string `json:"name"`
			} `json:"params"`
		}
		if json.NewDecoder(r.Body).Decode(&req) != nil || req.ID == nil {
			w.WriteHeader(http.StatusAccepted)
			return
		}
		var result any
		switch req.Method {
		case "initialize":
			result = map[string]any{"protocolVersion": protocolVersion, "capabilities": map[string]any{"tools": map[string]any{}}}
		case "tools/list":
			listed := []mcpTool{pinTool("a.b", "Original.")}
			if collision.Load() {
				listed = append(listed, pinTool(normalizeName("a.b"), "Collision."))
			}
			result = map[string]any{"tools": listed}
		case "tools/call":
			result = map[string]any{"content": []map[string]string{{"type": "text", "text": req.Params.Name}}}
		}
		writeHTTPRPCResult(w, req.ID, result)
	}))
	defer server.Close()
	host := NewHost()
	defer host.Close()
	spec := Spec{Name: "srv", Type: "http", URL: server.URL}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	tools, err := host.Add(ctx, spec)
	if !errors.Is(err, ErrAmbiguousToolCatalog) || len(tools) != 0 || host.HasClient(spec.Name) {
		t.Fatalf("ambiguous server connected: tools=%v err=%v", toolNames(tools), err)
	}
	collision.Store(false)
	tools, err = host.Add(ctx, spec)
	if err != nil {
		t.Fatal(err)
	}
	reg := tool.NewRegistry()
	for _, adapter := range tools {
		reg.Add(adapter)
	}
	resolved, canonical, ambiguous := reg.ResolveCall("a.b")
	if resolved == nil || canonical != toolName("srv", "a.b") || len(ambiguous) != 0 {
		t.Fatalf("recovered tool did not resolve: (%v, %q, %v)", resolved, canonical, ambiguous)
	}
	if output, err := resolved.Execute(ctx, json.RawMessage(`{}`)); err != nil || output != "a.b" {
		t.Fatalf("raw dispatch = (%q, %v), want a.b", output, err)
	}
}

func TestLegacySchemaCacheRejectsCollidingNames(t *testing.T) {
	redirectCache(t)
	spec := Spec{Name: "legacy", Type: "http", URL: "https://example.invalid/mcp?token=fixture"}
	key, ok := legacySchemaCacheKey(spec)
	if !ok {
		t.Fatal("fixture does not require migration")
	}
	cs := CachedSchema{CacheKey: key}
	for _, raw := range []string{"a.b", normalizeName("a.b")} {
		cs.Tools = append(cs.Tools, CachedTool{Name: raw, Schema: json.RawMessage(`{"type":"object"}`)})
	}
	if err := SaveCachedSchema(spec.Name, cs); err != nil {
		t.Fatal(err)
	}
	if loaded, ok := LoadCachedSchemaForSpec(spec); loaded != nil || ok {
		t.Fatal("legacy collision was migrated as a usable catalog")
	}
}

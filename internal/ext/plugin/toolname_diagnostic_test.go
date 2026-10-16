package plugin

import (
	"bytes"
	"encoding/json"
	"log"
	"log/slog"
	"strings"
	"testing"

	"reasonix/internal/contract/tool"
)

func TestRejectedCachedCatalogDiagnostic(t *testing.T) {
	for _, path := range []string{"current", "legacy", "direct"} {
		for _, disabled := range []bool{false, true} {
			t.Run(path+map[bool]string{false: "/rejected", true: "/disabled"}[disabled], func(t *testing.T) {
				redirectCache(t)
				spec := Spec{Name: "srv", Type: "http", URL: "https://example.invalid/mcp?token=fixture-secret",
					Env: map[string]string{"TOKEN": "fixture-secret"}, Headers: map[string]string{"Authorization": "Bearer fixture-secret"}}
				cs := CachedSchema{CacheKey: SchemaCacheKey(spec)}
				for _, raw := range []string{"a.fixture-secret", normalizeName("a.fixture-secret")} {
					cs.Tools = append(cs.Tools, CachedTool{Name: raw, Description: "fixture-secret",
						Schema: json.RawMessage(`{"type":"object","description":"fixture-secret"}`)})
				}
				if disabled {
					spec.DisabledTools = []string{cs.Tools[1].Name}
					cs.CacheKey = SchemaCacheKey(spec)
				}
				if path == "legacy" {
					var ok bool
					cs.CacheKey, ok = legacySchemaCacheKey(spec)
					if !ok {
						t.Fatal("fixture does not require migration")
					}
				}
				if err := SaveCachedSchema(spec.Name, cs); err != nil {
					t.Fatal(err)
				}
				var logs bytes.Buffer
				previous := slog.Default()
				writer, flags := log.Writer(), log.Flags()
				slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
				t.Cleanup(func() {
					slog.SetDefault(previous)
					log.SetOutput(writer)
					log.SetFlags(flags)
				})
				if path == "direct" {
					host := NewHost()
					defer host.Close()
					LazyToolset(spec, &cs, host, tool.NewRegistry(), t.Context(), false)
				} else {
					LoadCachedSchemaForSpec(spec)
				}
				if disabled {
					if logs.Len() != 0 {
						t.Fatalf("valid enabled catalog logged a rejection: %s", logs.String())
					}
					return
				}
				var record map[string]any
				if err := json.Unmarshal(logs.Bytes(), &record); err != nil {
					t.Fatalf("want one rejection diagnostic: %v", err)
				}
				if record["level"] != "WARN" || record["msg"] != "plugin cache: catalog rejected" ||
					record["server"] != spec.Name || record["err"] != ErrAmbiguousToolCatalog.Error() {
					t.Errorf("rejection reason missing: %v", record)
				}
				if strings.Contains(logs.String(), "fixture-secret") {
					t.Error("diagnostic leaked raw names, schema, description or credentials")
				}
			})
		}
	}
}

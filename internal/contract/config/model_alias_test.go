package config

import (
	"errors"
	"maps"
	"os"
	"strings"
	"testing"
)

func TestModelAliasConfigSurvivesBothRenderers(t *testing.T) {
	path := writeDisplayNameConfig(t)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	raw = []byte(strings.Replace(string(raw), `models = ["gpt-5", "gpt-5-mini"]`, `models = ["gpt-5", "gpt-5-mini"]`+"\n"+`model_aliases = { "gpt-5" = "简短名称", "gpt-5-mini" = "简短名称" }`, 1))
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	edit := LoadForEdit(path)
	p, _ := edit.Provider("relay")
	for _, render := range []func(*strings.Builder, ProviderEntry){renderProviderEntryPlain, renderProviderEntryAnnotated} {
		var b strings.Builder
		render(&b, *p)
		if !strings.Contains(b.String(), `model_aliases = { gpt-5 = "简短名称"`) {
			t.Fatalf("render lost model aliases:\n%s", b.String())
		}
	}
	if err := edit.SaveTo(path); err != nil {
		t.Fatal(err)
	}
	saved, _ := os.ReadFile(path)
	if !strings.Contains(string(saved), "model_aliases") {
		t.Fatalf("save lost aliases:\n%s", saved)
	}
	resolved, ok := LoadForEdit(path).ResolveModel("relay/gpt-5-mini")
	if !ok || resolved.Model != "gpt-5-mini" || resolved.ModelAlias("gpt-5-mini") != "简短名称" {
		t.Fatalf("wire model changed: %+v", resolved)
	}
}

func TestModelAliasesAreExactAtomicAndIsolated(t *testing.T) {
	e := ProviderEntry{Models: []string{"vendor/model", "Vendor/model"}, ModelAliases: map[string]string{"unlisted": "Retain"}}
	if err := e.SetModelAliases(map[string]string{"vendor/model": "  Same  ", "Vendor/model": "Same"}); err != nil {
		t.Fatal(err)
	}
	if e.ModelAlias("vendor/model") != "Same" || e.ModelAlias("Vendor/model") != "Same" || e.ModelAlias("vendor/Model") != "" {
		t.Fatalf("aliases lost exact ID matching: %+v", e.ModelAliases)
	}
	before := maps.Clone(e.ModelAliases)
	for _, tc := range []struct {
		aliases map[string]string
		want    error
	}{
		{map[string]string{"vendor/model": "Changed", "ghost": "No"}, ErrModelAliasUnlisted},
		{map[string]string{"vendor/model": "Changed", "Vendor/model": "a\tb"}, ErrProviderDisplayNameInvalid},
		{map[string]string{"vendor/model": strings.Repeat("名", 65)}, ErrProviderDisplayNameTooLong},
	} {
		if err := e.SetModelAliases(tc.aliases); !errors.Is(err, tc.want) || !maps.Equal(e.ModelAliases, before) {
			t.Fatalf("refused edit = %v, aliases = %+v", err, e.ModelAliases)
		}
	}
	cp := cloneProviderEntry(e)
	if err := cp.SetModelAliases(map[string]string{"vendor/model": strings.Repeat("名", 64)}); err != nil {
		t.Fatal(err)
	}
	cp.ModelAliases["unlisted"] = "Changed"
	if !maps.Equal(e.ModelAliases, before) {
		t.Fatal("editing a cloned provider changed the original")
	}
	if err := e.SetModelAliases(map[string]string{"vendor/model": "  "}); err != nil {
		t.Fatal(err)
	}
	if !maps.Equal(e.ModelAliases, map[string]string{"unlisted": "Retain"}) {
		t.Fatalf("reset did not preserve unlisted metadata: %+v", e.ModelAliases)
	}
}

func TestWireModelMigrationCarriesDisplayMetadata(t *testing.T) {
	e := ProviderEntry{Models: []string{"old", "new"}, ModelAliases: map[string]string{"old": "Old alias", "new": "Chosen alias"}}
	if !renameProviderModel(&e, "old", "new") || len(e.ModelAliases) != 1 || e.ModelAlias("new") != "Chosen alias" {
		t.Fatalf("wire migration lost the survivor's alias: %+v", e)
	}
}

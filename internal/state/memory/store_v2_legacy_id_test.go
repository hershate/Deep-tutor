package memory

import "testing"

// The legacy fact ID is a digest over a domain separator. It is not an
// internal detail: a fact migrated from the old store is filed under this ID,
// and the ID is what the index, the references and the archive point at.
// Changing the separator changes every ID the store has already handed out,
// and the old ones stop resolving — the same fact migrates twice, under two
// names.
//
// The value below is therefore a contract, not a snapshot. It was computed
// with the separator this store has always used.
func TestLegacyMemoryIDIsStableAcrossBuilds(t *testing.T) {
	cases := []struct {
		name  string
		scope FactScope
		want  string
	}{
		{"alpha", FactScopeProject, "legacy-3369e706e8d1ee96a2ab7627"},
		{"alpha", FactScopeGlobal, "legacy-26d465cbb9b3f2c0e18b3802"},
	}
	for _, c := range cases {
		if got := legacyMemoryID(c.name, c.scope); got != c.want {
			t.Errorf("legacyMemoryID(%q, %q) = %q, want %q", c.name, c.scope, got, c.want)
		}
	}
}

// The separator is part of the name, and the name is what the store looks a
// fact up by. A test that only compares two calls of the same function would
// pass with any separator at all.
func TestLegacyMemoryIDSeparatesNamesAndScopes(t *testing.T) {
	if legacyMemoryID("alpha", FactScopeProject) == legacyMemoryID("beta", FactScopeProject) {
		t.Error("two names share one legacy ID")
	}
	if legacyMemoryID("alpha", FactScopeProject) == legacyMemoryID("alpha", FactScopeGlobal) {
		t.Error("one name in two scopes shares one legacy ID")
	}
}

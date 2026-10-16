package delegation

import (
	"slices"
	"testing"
)

func TestSubagentsInheritTheGrantedRootsTheExecutorObserves(t *testing.T) {
	tool := &TaskTool{}
	tool.WithExtraRoots([]string{"/granted/a", "/granted/b"})
	got := tool.subagentOptions(t.Context(), 3, nil, 0, 1, "", nil)
	if !slices.Equal(got.ExtraWorkspaceRoots, []string{"/granted/a", "/granted/b"}) {
		t.Fatalf("child options carry %v", got.ExtraWorkspaceRoots)
	}
	if plain := (&TaskTool{}).subagentOptions(t.Context(), 3, nil, 0, 1, "", nil); len(plain.ExtraWorkspaceRoots) != 0 {
		t.Fatalf("a task tool without grants invented roots: %v", plain.ExtraWorkspaceRoots)
	}
}

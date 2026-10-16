package boot

import (
	"testing"

	"reasonix/internal/contract/tool"
)

func TestAddTutorPromoteGating(t *testing.T) {
	dir, work := t.TempDir(), t.TempDir()

	reg := tool.NewRegistry()
	addTutorPromote(reg, nil, "", work)
	if _, ok := reg.Get("promote_artifact"); ok {
		t.Fatal("no tutor dir should leave the tool out")
	}

	reg = tool.NewRegistry()
	addTutorPromote(reg, nil, dir, "")
	if _, ok := reg.Get("promote_artifact"); ok {
		t.Fatal("no workspace should leave the tool out")
	}

	reg = tool.NewRegistry()
	addTutorPromote(reg, []string{"write_file"}, dir, work)
	if _, ok := reg.Get("promote_artifact"); ok {
		t.Fatal("an explicit enabled list that omits the tool should leave it out")
	}

	reg = tool.NewRegistry()
	addTutorPromote(reg, nil, dir, work)
	got, ok := reg.Get("promote_artifact")
	if !ok {
		t.Fatal("a tutor dir and a workspace should register the tool")
	}
	if got.ReadOnly() {
		t.Fatal("promote_artifact writes, so it must not be read-only")
	}

	reg = tool.NewRegistry()
	addTutorPromote(reg, []string{"promote_artifact"}, dir, work)
	if _, ok := reg.Get("promote_artifact"); !ok {
		t.Fatal("an enabled list naming the tool should register it")
	}
}

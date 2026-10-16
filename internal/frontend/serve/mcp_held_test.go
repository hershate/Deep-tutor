package serve

import (
	"fmt"
	"strings"
	"testing"

	"reasonix/internal/contract/tool"
	"reasonix/internal/ext/plugin"
)

func TestMCPHeldViewsNameTheSetTheDigestCovers(t *testing.T) {
	held := []plugin.HeldTool{
		{RawName: "a", VisibleName: "mcp__s__a", Class: tool.HeldMCPAdded, Digest: "d1"},
		{RawName: "b", VisibleName: "mcp__s__b", Class: tool.HeldMCPChanged, Digest: "d2", Approved: "d0"},
	}
	views, digest, more := mcpHeldViews(held)
	if len(views) != 2 || more != 0 || views[0].Class != "added" || views[1].Class != "changed" {
		t.Fatalf("views = %+v more = %d", views, more)
	}
	if digest != plugin.HeldDigest(held) {
		t.Fatalf("digest %q is not the digest of the held set", digest)
	}
}

func TestMCPHeldViewsBoundTheRowButNotTheDigest(t *testing.T) {
	var held []plugin.HeldTool
	for i := range mcpHeldLimit + 7 {
		held = append(held, plugin.HeldTool{RawName: fmt.Sprint("t", i), VisibleName: fmt.Sprint("t", i), Class: tool.HeldMCPAdded, Digest: "d"})
	}
	views, digest, more := mcpHeldViews(held)
	if len(views) != mcpHeldLimit || more != 7 {
		t.Fatalf("listed %d, more %d", len(views), more)
	}
	if digest != plugin.HeldDigest(held) {
		t.Fatal("the digest must cover every withheld tool, including the ones past the cap")
	}
}

func TestMCPHeldViewsSanitiseServerWrittenNames(t *testing.T) {
	views, _, _ := mcpHeldViews([]plugin.HeldTool{{RawName: "x", VisibleName: "evil\x1b[31m" + strings.Repeat("n", mcpServerTextLimit*2), Class: tool.HeldMCPChanged, Digest: "d"}})
	if strings.ContainsRune(views[0].Name, 0x1b) || len([]rune(views[0].Name)) > mcpServerTextLimit+1 {
		t.Fatalf("name was not sanitised and bounded: %q", views[0].Name)
	}
	if v, d, m := mcpHeldViews(nil); v != nil || d != "" || m != 0 {
		t.Fatal("no held tools must produce no row fields")
	}
}

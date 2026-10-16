package plugin

import (
	"regexp"
	"strings"
	"testing"
)

var anthropicToolName = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,128}$`)

func TestModelToolNameStaysWithinTheProviderLimit(t *testing.T) {
	for _, raw := range []string{
		strings.Repeat("a", 150),
		strings.Repeat("a", 128-len(ToolPrefix("srv"))),
		strings.Repeat("a", 128-len(ToolPrefix("srv"))+1),
		strings.Repeat("长", 80),
		"weird name/" + strings.Repeat("x", 140),
	} {
		got := ModelToolName("srv", raw)
		if !anthropicToolName.MatchString(got) {
			t.Errorf("ModelToolName(srv, %.20q…) = %q (%d chars), outside the provider's name rule", raw, got, len(got))
		}
		if !strings.HasPrefix(got, ToolPrefix("srv")) {
			t.Errorf("%q lost the server prefix, so prefix removal would miss it", got)
		}
	}
}

func TestModelToolNameLeavesFittingNamesAlone(t *testing.T) {
	raw := strings.Repeat("a", 128-len(ToolPrefix("srv")))
	if got, want := ModelToolName("srv", raw), ToolPrefix("srv")+raw; got != want {
		t.Fatalf("a name that already fits changed: %q, want %q", got, want)
	}
}

func TestModelToolNameKeepsLongSiblingsApart(t *testing.T) {
	head := strings.Repeat("a", 140)
	a, b := ModelToolName("srv", head+"_one"), ModelToolName("srv", head+"_two")
	if a == b {
		t.Fatalf("two tools that differ only past the cut share the name %q", a)
	}
	if again := ModelToolName("srv", head+"_one"); again != a {
		t.Fatalf("the shortened name is not deterministic: %q then %q", a, again)
	}
}

func TestModelToolNameWithAnOversizedServerPrefix(t *testing.T) {
	server := strings.Repeat("s", 130)
	if got := ModelToolName(server, "tool"); !strings.HasPrefix(got, ToolPrefix(server)) {
		t.Fatalf("a server too long to leave room for a tool still owns its prefix: %q", got)
	}
}

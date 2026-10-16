package mcpsetup

import (
	"strings"
	"testing"

	"reasonix/internal/base/i18n"
	"reasonix/internal/base/textutil"
	"reasonix/internal/contract/config"
)

func TestLaunchLineSaysFirstWhenItDeletedCharacters(t *testing.T) {
	clean := LaunchLine(config.PluginEntry{Command: "node", Args: []string{"server.js", "two\nlines\tand tab"}})
	if strings.Contains(clean, i18n.M.LaunchHiddenRemoved) {
		t.Fatalf("a plain line was marked: %q", clean)
	}
	for name, e := range map[string]config.PluginEntry{
		"bidi": {Command: "node", Args: []string{"ser\u202ever.js"}},
		"zwj":  {Command: "node", Args: []string{"\U0001F468\u200d\U0001F469"}},
		"zwnj": {Command: "node", Args: []string{"\u0646\u200c\u0646"}},
		"url":  {URL: "https://h.example/\u200bx"},
	} {
		got := LaunchLine(e)
		if !strings.HasPrefix(got, i18n.M.LaunchHiddenRemoved+" ") {
			t.Errorf("%s: an altered line does not lead with the marker: %q", name, got)
		}
	}
}

// Callers cut a launch line to fit from its end; the marker must survive.
func TestLaunchLineMarkerSurvivesCuttingFromTheEnd(t *testing.T) {
	got := LaunchLine(config.PluginEntry{Command: "node", Args: []string{"a\u202eb", strings.Repeat("x", 500)}})
	cut := textutil.TruncateGraphemes(got, 40, "…")
	if !strings.HasPrefix(cut, i18n.M.LaunchHiddenRemoved) {
		t.Fatalf("the marker was cut: %q", cut)
	}
}

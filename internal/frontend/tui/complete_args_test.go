package tui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The selected prompt shows what it takes: each declared argument by name, a
// mark when it is required, and its description. Rows that are not selected
// stay one line.
func TestSelectedPromptListsItsDeclaredArguments(t *testing.T) {
	m, _ := testModel(t)
	m.composer.SetValue("/mcp__docs")
	m.menu = &menu{line: m.composer.Value(), sel: 0, c: Completion{Kind: "slash", Items: []CompletionItem{
		{Label: "/mcp__docs__summarize", Insert: "/mcp__docs__summarize ", Kind: "prompt", Args: []CompletionArg{
			{Name: "topic", Description: "what to cover", Required: true}, {Name: "length"},
		}, MoreArgs: 2},
		{Label: "/mcp__docs__other", Insert: "/mcp__docs__other ", Kind: "prompt", Args: []CompletionArg{{Name: "x"}}},
	}}}
	var plain []string
	for _, l := range m.menuLines() {
		plain = append(plain, strings.TrimSpace(ansi.Strip(l)))
	}
	got := strings.Join(plain, "\n")
	for _, want := range []string{"topic *  what to cover", "length", "… +2"} {
		if !strings.Contains(got, want) {
			t.Fatalf("menu lacks %q:\n%s", want, got)
		}
	}
	if strings.Contains(got, "\nx") {
		t.Fatalf("an unselected prompt's arguments are listed:\n%s", got)
	}
}

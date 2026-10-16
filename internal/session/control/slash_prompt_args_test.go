package control

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"reasonix/internal/ext/plugin"
)

// A prompt's declared arguments ride on its completion item, in the order the
// server declared them, so every frontend can show what the command takes.
func TestPromptCompletionItemCarriesItsArgumentDeclarations(t *testing.T) {
	item := promptSlashItem(plugin.Prompt{
		Name: "mcp__docs__summarize", Description: "summarize docs",
		Args: []plugin.PromptArg{
			{Name: "topic", Description: "what to cover", Required: true},
			{Name: "length", Required: false},
		},
	})
	want := []SlashArg{{Name: "topic", Description: "what to cover", Required: true}, {Name: "length"}}
	if !reflect.DeepEqual(item.Args, want) {
		t.Fatalf("Args = %+v, want %+v", item.Args, want)
	}
	if item.Label != "/mcp__docs__summarize" || item.Insert != "/mcp__docs__summarize " || item.Kind != "prompt" || item.Hint != "summarize docs" {
		t.Fatalf("item = %+v", item)
	}
	raw, _ := json.Marshal(item)
	var wire struct {
		Args []struct {
			Name     string `json:"name"`
			Required bool   `json:"required"`
		} `json:"args"`
	}
	if err := json.Unmarshal(raw, &wire); err != nil || len(wire.Args) != 2 || !wire.Args[0].Required {
		t.Fatalf("wire = %s (%v)", raw, err)
	}
}

func TestPromptWithoutArgumentsAddsNoArgsField(t *testing.T) {
	raw, _ := json.Marshal(promptSlashItem(plugin.Prompt{Name: "mcp__s__p"}))
	var m map[string]any
	_ = json.Unmarshal(raw, &m)
	if _, has := m["args"]; has {
		t.Fatalf("a prompt with no arguments carries args: %s", raw)
	}
}

// Declarations are data from a server: no escape sequence or control reaches a
// terminal or a row, and a long list or text is cut with the count kept.
func TestPromptCompletionItemStripsControlsAndBoundsTheDeclarations(t *testing.T) {
	var args []plugin.PromptArg
	for i := range 9 {
		args = append(args, plugin.PromptArg{Name: fmt.Sprintf("a%d", i)})
	}
	args[0] = plugin.PromptArg{
		Name:        "x\x1b[31mred\x1b]0;title\x07\nname",
		Description: "d\x1b[2Jone" + strings.Repeat("y", 500),
	}
	item := promptSlashItem(plugin.Prompt{Name: "mcp__s__p", Description: "hint\x1b[1mbold\r\nnext", Args: args})
	if len(item.Args) != 6 || item.MoreArgs != 3 {
		t.Fatalf("Args %d, MoreArgs %d, want 6 and 3", len(item.Args), item.MoreArgs)
	}
	raw, _ := json.Marshal(item)
	if strings.ContainsAny(string(raw), "\x1b\x07") || strings.Contains(string(raw), "\\u001b") || strings.Contains(string(raw), "\\n") {
		t.Fatalf("a control survived: %s", raw)
	}
	if n := len([]rune(item.Args[0].Description)); n > maxPromptArgDesc {
		t.Fatalf("description is %d runes", n)
	}
	if !strings.HasPrefix(item.Args[0].Name, "x") || strings.Contains(item.Args[0].Name, "red]") {
		t.Fatalf("name = %q", item.Args[0].Name)
	}
}

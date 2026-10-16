package tui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"reasonix/internal/contract/eventwire"
)

func workedRows(tr *Transcript, width int) []string {
	var rows []string
	for i := range tr.Items {
		if tr.Items[i].Kind == ItemWorked {
			rows = append(rows, ansi.Strip(renderItem(&tr.Items[i], width, 0, false)))
		}
	}
	return rows
}

func TestTurnDoneDrawsTheKernelsRunTime(t *testing.T) {
	tr := &Transcript{}
	tr.Apply(eventwire.Event{Kind: "turn_started"})
	tr.Apply(eventwire.Event{Kind: "message", Text: "done"})
	tr.Apply(eventwire.Event{Kind: "turn_done", WorkDurationMs: 65_000})
	rows := workedRows(tr, 80)
	if len(rows) != 1 || !strings.Contains(rows[0], "worked for 1m 5s") {
		t.Fatalf("worked rows = %q, want one saying 1m 5s", rows)
	}
}

func TestTurnDoneWithoutRunTimeDrawsNothing(t *testing.T) {
	for name, ev := range map[string]eventwire.Event{
		"none":      {Kind: "turn_done"},
		"too short": {Kind: "turn_done", WorkDurationMs: 300},
	} {
		tr := &Transcript{}
		tr.Apply(ev)
		if rows := workedRows(tr, 80); len(rows) != 0 {
			t.Errorf("%s drew %q", name, rows)
		}
	}
}

func TestRestoredAnswerKeepsItsRunTime(t *testing.T) {
	tr := &Transcript{}
	tr.Restore([]HistoryMessage{
		{Role: "user", Content: "q"},
		{Role: "assistant", Content: "a", WorkDurationMs: 3_725_000},
		{Role: "assistant", Content: "no time recorded"},
	})
	rows := workedRows(tr, 80)
	if len(rows) != 1 || !strings.Contains(rows[0], "worked for 1h 2m 5s") {
		t.Fatalf("worked rows = %q", rows)
	}
	if tr.Items[2].Kind != ItemWorked || tr.Items[1].Kind != ItemSay {
		t.Fatalf("run time must follow the answer it belongs to: %+v", tr.Items)
	}
}

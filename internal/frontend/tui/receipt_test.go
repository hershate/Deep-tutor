package tui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"reasonix/internal/base/i18n"
	"reasonix/internal/contract/eventwire"
)

// The card says what went unproven in the reader's language, keeps a kind it
// has no phrase for, and names what carried a clean verdict.
func TestReceiptCardNamesGapsAndEvidence(t *testing.T) {
	gaps := ansi.Strip(renderReceipt(&eventwire.CompletionReceipt{Verdict: "incomplete", Gaps: []eventwire.ReceiptGap{
		{Kind: "unverified_mutation", Detail: "a.go"},
		{Kind: "some_future_kind"},
	}}, 80, false))
	for _, want := range []string{i18n.M.ReceiptGapsHeader, "a.go", "some_future_kind"} {
		if !strings.Contains(gaps, want) {
			t.Fatalf("gap card lacks %q:\n%s", want, gaps)
		}
	}
	if phrase := i18n.M.ReceiptGapKinds["unverified_mutation"]; phrase != "" && !strings.Contains(gaps, phrase) {
		t.Fatalf("gap card lacks the phrase %q:\n%s", phrase, gaps)
	}

	clean := ansi.Strip(renderReceipt(&eventwire.CompletionReceipt{Verdict: "done",
		Changes:       []eventwire.ReceiptChange{{Path: "a.go"}},
		Verifications: []eventwire.ReceiptVerification{{Command: "go test ./...", Passed: true}}}, 80, false))
	if !strings.Contains(clean, i18n.M.ReceiptVerified) || !strings.Contains(clean, "go test ./...") {
		t.Fatalf("clean card = %q", clean)
	}
}

// Hidden, the card is replaced by one line that exists only when the kernel
// graded the receipt; the terminal never grades gaps itself.
func TestReceiptLineFollowsTheKernelsGrade(t *testing.T) {
	warn := ansi.Strip(renderReceipt(&eventwire.CompletionReceipt{Verdict: "partial", Severity: "warn",
		Changes: []eventwire.ReceiptChange{{Path: "a.go"}},
		Gaps:    []eventwire.ReceiptGap{{Kind: "unverified_change", Detail: "go vet"}}}, 80, true))
	if !strings.Contains(warn, "! "+i18n.M.ReceiptGapKinds["hint:gaps"]) || strings.Contains(warn, "go vet") {
		t.Fatalf("warn line = %q, want one line saying changes are unverified and no card", warn)
	}
	notice := ansi.Strip(renderReceipt(&eventwire.CompletionReceipt{Verdict: "incomplete", Severity: "notice",
		Unverified: []string{"UI not run"}}, 80, true))
	if !strings.Contains(notice, "· "+i18n.M.ReceiptGapKinds["hint:declared"]) {
		t.Fatalf("notice line = %q", notice)
	}
	if got := renderReceipt(&eventwire.CompletionReceipt{Verdict: "done", Changes: []eventwire.ReceiptChange{{Path: "a.go"}}}, 80, true); got != "" {
		t.Fatalf("an ungraded receipt drew %q while hidden", got)
	}
}

func TestReceiptIsALineUnlessTheConfigAsksForTheCard(t *testing.T) {
	ev := eventwire.Event{Kind: "turn_done", Receipt: &eventwire.CompletionReceipt{Verdict: "partial", SaysSomething: true, Severity: "warn",
		Gaps: []eventwire.ReceiptGap{{Kind: "unverified_change", Detail: "go vet"}}}}
	for _, tc := range []struct {
		name string
		show bool
	}{{"default hides the card", false}, {"show_receipt draws it", true}} {
		m := newModel(t.Context(), Options{ShowReceipt: tc.show})
		m.tr.Apply(ev)
		m.resetScreen()
		if m.tr.ReceiptLine == tc.show {
			t.Fatalf("%s: ReceiptLine = %v after a screen reset", tc.name, m.tr.ReceiptLine)
		}
		m.tr.Apply(ev)
		got := ansi.Strip(renderItem(&m.tr.Items[0], 80, 0, false))
		if card := strings.Contains(got, i18n.M.ReceiptGapsHeader); card != tc.show {
			t.Fatalf("%s: rendered %q", tc.name, got)
		}
	}
}

package event

import "testing"

// One rule, one place. A receipt shown in the window and swallowed in the
// terminal is the same turn described two ways, which is the drift this
// predicate exists to prevent.
func TestReceiptSaysSomething(t *testing.T) {
	for _, tc := range []struct {
		name string
		r    *CompletionReceipt
		want bool
	}{
		{"nil", nil, false},
		{"a clean verdict is sourced, not silent", &CompletionReceipt{Verdict: "done"}, true},
		{"a gap is the whole point", &CompletionReceipt{Verdict: "partial", Gaps: []ReceiptGap{{Kind: "unreviewed_change"}}}, true},
		// Declarations used to ride in as gaps. They no longer do, so a receipt
		// carrying only one still has to be worth showing or the honest answer
		// disappears from every surface at once.
		{"a declared caveat", &CompletionReceipt{Verdict: "incomplete", Unverified: []string{"UI never exercised"}}, true},
		{"a declared risk", &CompletionReceipt{Verdict: "incomplete", Risks: []string{"one-way migration"}}, true},
		{"nothing judged", &CompletionReceipt{Verdict: "incomplete"}, false},
	} {
		if got := tc.r.SaysSomething(); got != tc.want {
			t.Errorf("%s: SaysSomething() = %v, want %v", tc.name, got, tc.want)
		}
	}
}

func TestReceiptSeverity(t *testing.T) {
	gap := func(kind string) *CompletionReceipt {
		return &CompletionReceipt{Verdict: "partial", Gaps: []ReceiptGap{{Kind: kind}}}
	}
	for _, tc := range []struct {
		name string
		r    *CompletionReceipt
		want string
	}{
		{"nil", nil, ReceiptSeverityNone},
		{"clean", &CompletionReceipt{Verdict: "done"}, ReceiptSeverityNone},
		{"a change nothing proves", gap("unverified_change"), ReceiptSeverityWarn},
		{"a failed check", gap("failed_verification"), ReceiptSeverityWarn},
		{"a verification that predates the change", gap("stale_verification"), ReceiptSeverityNotice},
		{"a gap kind this host does not know", gap("something_new"), ReceiptSeverityWarn},
		{"a declared caveat alone", &CompletionReceipt{Verdict: "incomplete", Unverified: []string{"UI not run"}}, ReceiptSeverityNotice},
		{"the worse of two gaps wins", &CompletionReceipt{Verdict: "partial", Gaps: []ReceiptGap{{Kind: "unreviewed_change"}, {Kind: "missing_check"}}}, ReceiptSeverityWarn},
		{"a caveat does not soften a gap", &CompletionReceipt{Verdict: "partial", Risks: []string{"x"}, Gaps: []ReceiptGap{{Kind: "unverified_change"}}}, ReceiptSeverityWarn},
	} {
		if got := tc.r.Severity(); got != tc.want {
			t.Errorf("%s: Severity() = %q, want %q", tc.name, got, tc.want)
		}
	}
}

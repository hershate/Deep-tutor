package eventwire

import (
	"encoding/json"
	"strings"
	"testing"

	"reasonix/internal/contract/event"
)

func TestReceiptWireCarriesTheKernelsSeverity(t *testing.T) {
	warn := completionReceiptWire(&event.CompletionReceipt{Verdict: "partial", Gaps: []event.ReceiptGap{{Kind: "unverified_change"}}})
	if warn.Severity != event.ReceiptSeverityWarn {
		t.Fatalf("severity = %q, want warn", warn.Severity)
	}
	clean, err := json.Marshal(completionReceiptWire(&event.CompletionReceipt{Verdict: "done"}))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(clean), "severity") {
		t.Fatalf("a clean receipt carries a severity field: %s", clean)
	}
}

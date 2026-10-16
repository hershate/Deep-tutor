package computer

import (
	"strings"
	"testing"
)

func TestDriverRefusalReasonIsQuotedAndBounded(t *testing.T) {
	err := checkDriverResult(DriverResult{IsError: true, Content: []DriverContent{{Type: "text", Text: "source\n\x00" + strings.Repeat("x", 10000)}}})
	if CodeOf(err) != CodeDriverRefused {
		t.Fatal(err)
	}
	if strings.ContainsAny(err.Error(), "\n\x00") || len(err.Error()) > 2300 {
		t.Fatalf("unsafe diagnostic of %d bytes", len(err.Error()))
	}
}

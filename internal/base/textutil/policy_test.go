package textutil

import (
	"strings"
	"testing"
)

const hostile = "run\u200b \u202eevil\x1b]0;title\x07 x\u2060y"

func TestLocatorPolicyShowsEveryHiddenCharacter(t *testing.T) {
	shown := ShownLocator(hostile)
	if strings.ContainsAny(shown, "\u200b\u202e\x1b\x07\u2060") {
		t.Fatalf("a hidden character stayed hidden: %q", shown)
	}
	for _, want := range []string{`\u{200b}`, `\u{202e}`, `\u{1b}`, `\u{2060}`} {
		if !strings.Contains(shown, want) {
			t.Fatalf("locator policy hid %s: %q", want, shown)
		}
	}
}

func TestDeletingPoliciesAgreeOnTheSameFixture(t *testing.T) {
	prose := strings.Join(strings.Fields(ShownProse(hostile)), " ")
	if display := SanitizeDisplay(hostile); display != prose {
		t.Fatalf("display reads %q but prose reads %q", display, prose)
	}
	launch, removed := ScrubLaunch(hostile)
	if !removed || strings.ContainsAny(launch, "\u200b\u202e\u2060\x1b") {
		t.Fatalf("launch = %q removed=%v", launch, removed)
	}
	stored := SanitizeStored(hostile)
	if strings.ContainsAny(stored, "\u200b\u202e\u2060\x1b\x07") {
		t.Fatalf("stored kept an invisible character: %q", stored)
	}
}

func TestLaunchReportsExactlyWhatItDeleted(t *testing.T) {
	for in, want := range map[string]bool{
		"plain text \u65e5\u672c\u8a9e": false,
		"line\nbreak\tand  ":            false,
		"a\u200bb":                      true,
		"a\u202eb":                      true,
		"\U0001f468\u200d\U0001f469":    true,
		"\u0646\u200c\u0646":            true,
		"a\x1b]0;t\x07b":                true,
	} {
		if _, got := ScrubLaunch(in); got != want {
			t.Errorf("ScrubLaunch(%q) removed=%v, want %v", in, got, want)
		}
	}
}

func TestSanitizeStoredKeepsLinesAndDeletesTheRest(t *testing.T) {
	in := "a\x00b\u202ec\u2066d\u200fe\x1b[31mf\tg\nh\u200bi\u200cj\u200dk\u2060l\ufeffm\U000e0041n\u2028o\u2029p\rq"
	if got := SanitizeStored(in); got != "abcde[31mf\tg\nhijklmnopq" {
		t.Fatalf("got %q", got)
	}
}

func TestDisplayDropsBidiLikeProse(t *testing.T) {
	if got := SanitizeDisplay("ab\u202ecd\u2066ef"); got != "abcdef" {
		t.Fatalf("got %q", got)
	}
}

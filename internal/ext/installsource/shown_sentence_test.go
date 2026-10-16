package installsource

import (
	"strings"
	"testing"
)

func TestSkippedMarketplaceWarningShowsAnUnterminatedEscapeInsteadOfEatingIt(t *testing.T) {
	got := skippedMarketplaceWarning("demo", "bad source\x1b]0;")
	if strings.ContainsRune(got, 0x1b) || !strings.Contains(got, `\u{1b}]0;`) || !strings.HasPrefix(got, `skipped Claude marketplace plugin "demo": `) {
		t.Fatalf("got %q", got)
	}
}

func TestSkillSentencesShowPathsAsLiterals(t *testing.T) {
	for _, got := range []string{
		shadowedSkillWarning("s", "/a\x1b]0;", "/b\x1b]0;"),
		unselectedSkillWarning("s", "/a\x1b]0;", "/b\x1b]0;"),
	} {
		if strings.ContainsRune(got, 0x1b) || strings.Count(got, `\u{1b}]0;`) != 2 {
			t.Fatalf("got %q", got)
		}
	}
}

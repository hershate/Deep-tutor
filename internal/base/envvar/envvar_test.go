package envvar

import "testing"

// The rename has to leave every machine that configured the old name working,
// and every machine that configures the new name in control of its own
// value. Both halves of that are load-bearing: the fallback is what keeps an
// existing installation's home where it is, and the preference is what lets a
// user move it.
func TestGetPrefersTheCurrentSpelling(t *testing.T) {
	t.Setenv("DEEPTUTOR_HOME", "F:/new-home")
	t.Setenv("REASONIX_HOME", "F:/old-home")
	if got := Get("DEEPTUTOR_HOME"); got != "F:/new-home" {
		t.Fatalf("Get(DEEPTUTOR_HOME) = %q, want the current spelling", got)
	}
}

func TestGetFallsBackToTheLegacySpelling(t *testing.T) {
	// The premise is that only the legacy spelling is set, so the current one
	// is stated empty rather than left to whatever the machine carries.
	t.Setenv("DEEPTUTOR_HOME", "")
	t.Setenv("REASONIX_HOME", "F:/old-home")
	if got := Get("DEEPTUTOR_HOME"); got != "F:/old-home" {
		t.Fatalf("Get(DEEPTUTOR_HOME) = %q, want the legacy spelling", got)
	}
}

func TestGetIsEmptyWhenNeitherIsSet(t *testing.T) {
	t.Setenv("DEEPTUTOR_STATE_HOME", "")
	t.Setenv("REASONIX_STATE_HOME", "")
	if got := Get("DEEPTUTOR_STATE_HOME"); got != "" {
		t.Fatalf("Get(DEEPTUTOR_STATE_HOME) = %q, want empty", got)
	}
}

// A name this product did not rename is read as it stands. A variable that
// happens to start with the current prefix but has no legacy spelling keeps
// working, and one that has neither prefix is untouched.
func TestGetLeavesOtherNamesAlone(t *testing.T) {
	t.Setenv("DEEPTUTOR_ONLY_NEW", "value")
	if got := Get("DEEPTUTOR_ONLY_NEW"); got != "value" {
		t.Fatalf("Get(DEEPTUTOR_ONLY_NEW) = %q, want the value as set", got)
	}
	t.Setenv("SOME_OTHER_VAR", "other")
	if got := Get("SOME_OTHER_VAR"); got != "other" {
		t.Fatalf("Get(SOME_OTHER_VAR) = %q, want the value as set", got)
	}
}

func TestLookupTellsUnsetFromEmpty(t *testing.T) {
	t.Setenv("DEEPTUTOR_HOME", "")
	if _, ok := Lookup("DEEPTUTOR_HOME"); ok {
		t.Fatal("Lookup reported a variable that is not set")
	}
	t.Setenv("REASONIX_HOME", "F:/old-home")
	v, ok := Lookup("DEEPTUTOR_HOME")
	if !ok || v != "F:/old-home" {
		t.Fatalf("Lookup = (%q, %v), want the legacy value", v, ok)
	}
}

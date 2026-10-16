package agent

import (
	"context"
	"errors"
	"strings"
	"testing"

	"reasonix/internal/contract/provider"
	"reasonix/internal/state/sessionstore"
)

// The user's own words are what no later step can rebuild: the workspace still
// holds the code a constraint governs, nothing holds the constraint. A fold the
// summarizer could not digest has to carry them, shortened, or the model is told
// that earlier messages exist and given none of them.
func TestMechanicalFoldCarriesTheUsersWordsShortened(t *testing.T) {
	long := "never touch the billing module " + strings.Repeat("because audit asked for it ", 40)
	fold := []provider.Message{
		{Role: provider.RoleUser, Content: long},
		{Role: provider.RoleAssistant, Content: "assistant chatter that must not ride along"},
		{Role: provider.RoleTool, Content: "tool output that must not ride along"},
		{Role: provider.RoleUser, Content: "prefer table-driven tests"},
		{Role: provider.RoleUser, Content: "local-only note", LocalOnly: true},
	}
	digest := mechanicalFoldDigest(fold)
	for _, want := range []string{"never touch the billing module", "prefer table-driven tests", "summary was unavailable", "5 earlier message"} {
		if !strings.Contains(digest, want) {
			t.Errorf("digest lacks %q:\n%s", want, digest)
		}
	}
	for _, banned := range []string{"assistant chatter", "tool output", "local-only note"} {
		if strings.Contains(digest, banned) {
			t.Errorf("digest carries %q:\n%s", banned, digest)
		}
	}
	if strings.Contains(digest, long) {
		t.Errorf("a long turn rode whole; it must be shortened")
	}
	if strings.Index(digest, "billing") > strings.Index(digest, "table-driven") {
		t.Errorf("the user's turns are not in the order they were said:\n%s", digest)
	}
}

func TestMechanicalFoldStaysBoundedForAHugeNumberOfTurns(t *testing.T) {
	var fold []provider.Message
	for range 3000 {
		fold = append(fold, provider.Message{Role: provider.RoleUser, Content: strings.Repeat("x", 600)})
	}
	if got := len(mechanicalFoldDigest(fold)); got > mechanicalUserTotalChars+1500 {
		t.Fatalf("digest is %d chars for 3000 turns, want it near the %d cap", got, mechanicalUserTotalChars)
	}
}

func TestADegradedCompactionKeepsWhatTheUserSaidThatTheBudgetCouldNotHold(t *testing.T) {
	big := strings.Repeat("word ", 400)
	msgs := []provider.Message{{Role: provider.RoleSystem, Content: "sys"}, {Role: provider.RoleUser, Content: "first task"}}
	for i := range 8 {
		msgs = append(msgs,
			provider.Message{Role: provider.RoleAssistant, Content: big},
			provider.Message{Role: provider.RoleUser, Content: "constraint-" + string(rune('a'+i)) + " " + strings.Repeat("detail ", 300)},
		)
	}
	a := agentOverForce(t, &fakeProvider{streamErr: errors.New("summarizer rejected the request")}, &sessionstore.Session{Messages: msgs})
	a.budgets.UserTurnKeepTokens = 200 // far below what the user turns cost: they cannot be held whole
	if err := prepareContext(context.Background(), a, CompactionTriggerOverflow); err != nil {
		t.Fatalf("prepare = %v", err)
	}
	digest := latestDigest(a.sess.win.compactionState.Projection.Messages)
	if !degradedFold(a) {
		t.Fatalf("expected a degraded fold, digest = %q", digest)
	}
	for _, c := range []string{"constraint-a", "constraint-d"} {
		if !strings.Contains(digest, c) {
			t.Errorf("the degraded digest dropped %q along with its turn:\n%s", c, digest)
		}
	}
}

// A summarizer request is one call that must fit a model whose real limit this
// host only knows through what was configured, and whose tokenizer this host
// only estimates (inline images are the case where the two part furthest). The
// input is capped well below the largest windows so that gap has room.
func TestSummaryInputIsCappedBelowTheLargestWindows(t *testing.T) {
	huge := agentOverForceWindow(t, &fakeProvider{}, &sessionstore.Session{}, 1_000_000)
	if got := huge.window().summaryInputBudget(""); got <= 0 || got > summaryInputCeilingTokens {
		t.Fatalf("budget for a 1M window = %d, want within (0, %d]", got, summaryInputCeilingTokens)
	}
	small := agentOverForceWindow(t, &fakeProvider{}, &sessionstore.Session{}, 64_000)
	if got := small.window().summaryInputBudget(""); got <= 0 || got >= 64_000 {
		t.Fatalf("budget for a 64k window = %d, want it governed by the window, not the ceiling", got)
	}
}

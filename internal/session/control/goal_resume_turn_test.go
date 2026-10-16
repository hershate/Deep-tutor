package control

import (
	"fmt"
	"testing"
	"time"

	"reasonix/internal/base/i18n"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/runtime/goaleval"
)

func TestGoalResumeCommandContinuesTheGoalAndSaysSo(t *testing.T) {
	prov := &scriptedTurns{turns: [][]provider.Chunk{textTurn("picked it back up")}}
	c, _, events := goalRuntimeController(t, prov, &fakeGoalEvaluator{outcome: goaleval.OutcomeComplete, reason: "done"})
	c.SetGoal("finish the migration")
	if !c.PauseGoal() {
		t.Fatal("goal did not pause")
	}
	for len(events) > 0 {
		<-events
	}
	// Completing the goal clears its text, which can happen before the notice
	// is read here, so the expected line is fixed while the goal is still set.
	want := fmt.Sprintf(i18n.M.GoalResumedFmt, ShortGoalForNotice(c.Goal()))

	c.Submit("/goal resume")

	sawNotice := false
	deadline := time.After(10 * time.Second)
	for done := false; !done; {
		select {
		case e := <-events:
			sawNotice = sawNotice || (e.Kind == event.Notice && e.Text == want)
			done = e.Kind == event.TurnDone
		case <-deadline:
			t.Fatal("no turn ran after /goal resume")
		}
	}
	if !sawNotice {
		t.Error("resuming the goal said nothing")
	}
	if prov.call != 1 {
		t.Errorf("provider calls = %d, want the resumed goal to run one turn", prov.call)
	}
	if got := c.GoalStatus(); got != GoalStatusComplete {
		t.Errorf("GoalStatus() = %q, want complete", got)
	}
}

func TestUserReplyToABlockedGoalPutsItBackInPursuit(t *testing.T) {
	prov := &scriptedTurns{turns: [][]provider.Chunk{textTurn("I need the token."), textTurn("got it, continuing")}}
	eval := &fakeGoalEvaluator{outcome: goaleval.OutcomeBlocked, reason: "needs the token only the user has"}
	c, _, events := goalRuntimeController(t, prov, eval)
	c.Submit("/goal finish the migration")
	waitGoalTurnDone(t, events)
	if got := c.GoalStatus(); got != GoalStatusBlocked {
		t.Fatalf("GoalStatus() = %q, want blocked", got)
	}

	c.Submit("the token is abc123")
	waitGoalTurnDone(t, events)

	if eval.calls != 2 {
		t.Errorf("evaluator calls = %d, want the reply to run as a turn of the goal (2)", eval.calls)
	}
}

func TestUserReplyToAPausedGoalStaysOutsideIt(t *testing.T) {
	prov := &scriptedTurns{turns: [][]provider.Chunk{textTurn("noted")}}
	eval := &fakeGoalEvaluator{outcome: goaleval.OutcomeComplete, reason: "done"}
	c, _, events := goalRuntimeController(t, prov, eval)
	c.SetGoal("finish the migration")
	c.PauseGoal()

	c.Submit("what is the status?")
	waitGoalTurnDone(t, events)

	if eval.calls != 0 {
		t.Errorf("evaluator calls = %d, want a manual pause to stay the user's to lift", eval.calls)
	}
}

package control

import (
	"fmt"
	"strings"

	"reasonix/internal/base/i18n"
	"reasonix/internal/contract/event"
)

// Two host decisions follow from a turn having recorded a user gate: the Goal
// pauses instead of driving through it, and the user is told what it waits for.

// stopCauseUserGate is the Goal pause a gate produces, and the only cause the
// next user turn lifts.
const stopCauseUserGate = "await_user"

// turnUserGate reports what the finished turn waits for, empty when nothing.
func (c *Controller) turnUserGate() string {
	if c.executor == nil {
		return ""
	}
	gate, ok := c.executor.UserGate()
	if !ok {
		return ""
	}
	return strings.TrimSpace(gate.Need)
}

// noticeUserGate reports the wait outside a Goal. Under one the FSM's pause
// notice already carries it.
func (c *Controller) noticeUserGate() {
	if c.goals.active() {
		return
	}
	if need := c.turnUserGate(); need != "" {
		c.sink.Emit(event.Event{Kind: event.Notice, Level: event.LevelInfo, Code: event.NoticeCodeAwaitUser,
			Text: fmt.Sprintf(i18n.M.AwaitingUserFmt, need), Detail: need})
	}
}

// liftUserGatedGoal resumes a Goal waiting on the user, the starting turn being
// the message it waits for: one gated by await_user, or one the model or the
// evaluator reported blocked. Other pause causes stay the user's to lift.
func (c *Controller) liftUserGatedGoal(synthetic bool) {
	if synthetic || !c.goals.awaitsUser() {
		return
	}
	c.ResumeGoal()
}

// awaitsUser reports a Goal that cannot go on without something from the user:
// gated by await_user, or blocked by a report with no pause cause of its own.
func (g *goalMachine) awaitsUser() bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.stopCause == stopCauseUserGate || (g.status == GoalStatusBlocked && g.stopCause == "")
}

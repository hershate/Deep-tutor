package control

import (
	"context"
	"fmt"

	"reasonix/internal/base/i18n"
)

func (c *Controller) startGoalCommandTurn(cmd GoalCommand, display string) {
	if !c.goals.active() {
		return
	}
	c.notice(fmt.Sprintf(i18n.M.GoalSetFmt, ShortGoalForNotice(c.Goal())))
	if c.runner != nil {
		c.runGuarded(func(ctx context.Context) error {
			return c.runTurnLoop(ctx, orchestratedTurn{
				input: "Start pursuing the active goal now.", raw: cmd.Text, display: display,
			})
		})
	}
}

// startGoalResumeTurn continues a Goal the user just re-entered: resuming means
// pursuing it again, so the line that asked for it is also the turn that does.
func (c *Controller) startGoalResumeTurn(display string) {
	c.notice(fmt.Sprintf(i18n.M.GoalResumedFmt, ShortGoalForNotice(c.Goal())))
	if c.runner != nil {
		c.runGuarded(func(ctx context.Context) error {
			return c.runTurnLoop(ctx, orchestratedTurn{
				input: "Resume pursuing the active goal now.", raw: "/goal resume", display: display,
			})
		})
	}
}

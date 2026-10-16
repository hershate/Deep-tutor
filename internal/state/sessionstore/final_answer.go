package sessionstore

import "reasonix/internal/contract/provider"

// TurnWorkMs says, for each authored turn, how long its runs took, indexed by
// the message that closed it. A run is the stretch between two user messages
// that are not steers; its figure is the largest an answer in it reports, and
// the turn's is their sum. Messages that close no turn read 0.
func TurnWorkMs(msgs []provider.Message) []int64 {
	out := make([]int64, len(msgs))
	var turn, run int64
	closing := -1
	closeRun := func() { turn, run = turn+run, 0 }
	closeTurn := func() {
		closeRun()
		if closing >= 0 {
			out[closing] = turn
		}
		turn, closing = 0, -1
	}
	for i, m := range msgs {
		switch {
		case m.Role == provider.RoleUser:
			class := ClassifyTurn(m, 0)
			switch {
			case class.Steer:
			case class.StartsTurn:
				closeTurn()
			default:
				closeRun()
			}
		case m.Role == provider.RoleAssistant && len(m.ToolCalls) == 0 && m.WorkDurationMs > 0:
			run, closing = max(run, m.WorkDurationMs), i
		}
	}
	closeTurn()
	return out
}

package tui

import (
	tea "charm.land/bubbletea/v2"

	"reasonix/internal/frontend/termrender"
)

// reportedStatus is the last record the terminal was sent.
type reportedStatus struct {
	state termrender.ProgramState
	kind  termrender.BlockedKind
}

// terminalStatus is what the terminal should be told about this screen now: a
// pending approval or question blocks, a running turn works, a turn that just
// ended is done, and a screen that has never run is idle.
func (m *model) terminalStatus() (termrender.ProgramState, termrender.BlockedKind) {
	if open := m.tr.OpenPrompt(); open != nil {
		if open.Kind == ItemAsk {
			return termrender.StateBlocked, termrender.BlockedQuestion
		}
		return termrender.StateBlocked, termrender.BlockedPermission
	}
	if m.tr.Running {
		return termrender.StateWorking, termrender.BlockedNone
	}
	if m.status7501.state == "" || m.status7501.state == termrender.StateIdle {
		return termrender.StateIdle, termrender.BlockedNone
	}
	return termrender.StateDone, termrender.BlockedNone
}

// reportStatus emits an OSC 7501 record when the state changed since the last
// one, and nothing otherwise.
func (m *model) reportStatus() tea.Cmd {
	if !m.reportOn {
		return nil
	}
	state, kind := m.terminalStatus()
	if now := (reportedStatus{state, kind}); now == m.status7501 {
		return nil
	}
	m.status7501 = reportedStatus{state, kind}
	return tea.Raw(termrender.StatusSequence(state, kind))
}

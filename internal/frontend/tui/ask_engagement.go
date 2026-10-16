package tui

type askEngagement int

const (
	askIdle askEngagement = iota
	askEngaging
	askEngaged
)

type askEngagementMsg struct {
	item int
	err  error
}

func (m *model) onAskEngagement(msg askEngagementMsg) {
	if m.ask == nil || m.ask.item != msg.item {
		return
	}
	if msg.err != nil {
		m.ask.engagement = askIdle
		m.tr.AddNotice("error", msg.err.Error())
		return
	}
	m.ask.engagement = askEngaged
}

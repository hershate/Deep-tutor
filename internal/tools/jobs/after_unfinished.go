package jobs

// AfterUnfinished runs fn once every job that has not fully exited by now has
// done so; at once when there is none. Jobs started later do not delay it.
func (m *Manager) AfterUnfinished(fn func()) {
	m.mu.Lock()
	var pending []chan struct{}
	for _, key := range m.order {
		j := m.jobs[key]
		select {
		case <-j.done:
		default:
			pending = append(pending, j.done)
		}
	}
	m.mu.Unlock()
	if len(pending) == 0 {
		fn()
		return
	}
	go func() {
		for _, done := range pending {
			<-done
		}
		fn()
	}()
}

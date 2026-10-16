// host_notice.go — what the host has to tell the model about state that moved
// under it, delivered on the turn tail.
package agent

import (
	"slices"
	"strings"
	"sync"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/state/sessionstore"
)

// maxPendingHostNotices bounds what a host that keeps changing its mind can
// append to one turn. A notice the model never reads is a notice it does not
// need: the surface it describes is readable at any time.
const maxPendingHostNotices = 8

// hostNoticeQueue is runtime-authored fact waiting for a round boundary. Unlike
// steerInbox it accepts while no turn is running: the fact is true from the
// moment it is queued, and the next turn is the first one that can act on it.
type hostNoticeQueue struct {
	mu      sync.Mutex
	pending []string
}

// NoteHostFact queues one sentence for the next round's turn tail. It is
// appended after the cached prefix, never into it, so telling the model costs
// no prefix rewrite. A fact already waiting is not repeated.
func (a *Agent) NoteHostFact(text string) {
	text = strings.TrimSpace(text)
	if a == nil || text == "" {
		return
	}
	a.sess.hostNotices.mu.Lock()
	defer a.sess.hostNotices.mu.Unlock()
	if slices.Contains(a.sess.hostNotices.pending, text) {
		return
	}
	if len(a.sess.hostNotices.pending) >= maxPendingHostNotices {
		return
	}
	a.sess.hostNotices.pending = append(a.sess.hostNotices.pending, text)
}

// takeHostFacts hands over everything queued, leaving the queue empty.
func (a *Agent) takeHostFacts() []string {
	if a == nil {
		return nil
	}
	a.sess.hostNotices.mu.Lock()
	defer a.sess.hostNotices.mu.Unlock()
	pending := a.sess.hostNotices.pending
	a.sess.hostNotices.pending = nil
	return pending
}

// appendTurnTailNotices states what the host knows and the model does not, on
// the turn tail rather than in the cached prefix: an append leaves the prefix
// byte-stable, so telling the model what moved under the session costs no
// cache miss.
func (a *Agent) appendTurnTailNotices() {
	for _, fact := range a.takeHostFacts() {
		a.sess.conversation.Add(provider.Message{Role: provider.RoleUser, Content: sessionstore.MidTurnSteerMessage(fact, true)})
		a.svc.sink.Emit(event.Event{Kind: event.Notice, Level: event.LevelInfo, Text: fact})
	}
}

package boot

import (
	"deep-tutor/internal/state/history"
	"deep-tutor/internal/state/sessionstore"
)

func newObservedSession(systemPrompt string) *sessionstore.Session {
	session := sessionstore.NewSession(systemPrompt)
	session.SetPersistObserver(history.PersistObserver())
	return session
}

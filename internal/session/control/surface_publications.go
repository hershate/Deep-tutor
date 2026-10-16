package control

import (
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/state/sessionstore"
)

type liveSurfacePublications struct {
	session *sessionstore.Session
	ids     map[string]string
	order   []string
}

func (c *Controller) PublishNativeSurface(callID, surfaceID string) error {
	c.snapshotMu.Lock()
	defer c.snapshotMu.Unlock()
	if c.executor == nil || !c.NativeSurfaces() {
		return surfacespec.ErrExpired
	}
	session := c.executor.Session()
	state := &c.surfacePublications
	if state.session != session {
		*state = liveSurfacePublications{session: session, ids: map[string]string{}}
	}
	if _, exists := state.ids[callID]; !exists {
		if len(state.order) == 64 {
			delete(state.ids, state.order[0])
			state.order = state.order[1:]
		}
		state.order = append(state.order, callID)
	}
	state.ids[callID] = surfaceID
	return nil
}

func (c *Controller) surfacePublished(callID, surfaceID string) bool {
	return c.executor != nil && c.surfacePublications.session == c.executor.Session() && c.surfacePublications.ids[callID] == surfaceID
}

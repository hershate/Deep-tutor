package control

import (
	"encoding/json"
	"errors"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/state/sessioninbox"
	"strings"
)

func (c *Controller) DeleteInboxItem(id string) error {
	c.snapshotMu.Lock()
	defer c.snapshotMu.Unlock()
	var action *surfacespec.Action
	if _, env, readErr := c.ReadInboxItem(id); readErr == nil {
		var pending surfacespec.Action
		if json.Unmarshal([]byte(env.Extra[surfacespec.ActionMessageName]), &pending) == nil && pending.ValidateIdentity() == nil {
			action = &pending
		}
	}
	if err := c.deleteInboxItem(id); err != nil {
		return err
	}
	if action != nil {
		if err := c.recordSurfaceAction(SurfaceActionRecord{Action: *action, ItemID: id, Status: "withdrawn"}); err != nil {
			return err
		}
		c.saveSurfaceActionReceipt()
		return nil
	}
	return c.surfaceActionWithdrawn(id)
}

func (c *Controller) deleteInboxItem(id string) error {
	st, err := c.ensureInbox()
	if err != nil {
		return err
	}
	switch dropped, dropErr := c.DropQueuedSteer(id); {
	case dropErr != nil:
		return dropErr
	case dropped:
		return nil
	}
	if err := st.DeleteItem(id); !errors.Is(err, sessioninbox.ErrInvalidState) {
		return err
	}
	// Not in the queue and not pending: for a steer that means the turn read it
	// already, which is a different answer than "wrong state for a delete".
	if meta, _, readErr := st.ReadItem(id); readErr == nil && steerAlreadyAdmitted(meta.State) {
		return ErrSteerApplied
	}
	return sessioninbox.ErrInvalidState
}

// CancelWithInboxItems stops the active turn and discards only the durable
// pending items explicitly owned by the cancelling frontend. Admission is
// paused around the batch deletion so TurnDone cannot race a cancelled item
// into a new provider turn. Unrelated inbox items remain intact.
func (c *Controller) CancelWithInboxItems(ids []string, source string) error {
	c.snapshotMu.Lock()
	defer c.snapshotMu.Unlock()
	st, err := c.ensureInbox()
	if err != nil {
		c.Cancel()
		return err
	}
	wasPaused := st.Snapshot().Paused
	if err := st.SetPaused(true); err != nil {
		c.Cancel()
		return err
	}
	var withdrawn []string
	for _, id := range ids {
		meta, _, readErr := st.ReadItem(id)
		if readErr == nil && (source == "" || meta.Source == source) && (meta.State == sessioninbox.StateQueued || meta.State == sessioninbox.StateBlocked || meta.State == sessioninbox.StateUncertain) {
			withdrawn = append(withdrawn, id)
		}
	}
	if err := st.DiscardPendingItemsOwned(ids, strings.TrimSpace(source)); err != nil {
		// Keep the inbox paused for inspection if an item already crossed the
		// admission boundary. Cancellation still stops that in-flight turn.
		c.Cancel()
		return err
	}
	c.Cancel()
	for _, id := range withdrawn {
		if err := c.surfaceActionWithdrawn(id); err != nil {
			return err
		}
	}
	if !wasPaused {
		if err := st.SetPaused(false); err != nil {
			return err
		}
		c.maybeDispatchInbox()
	}
	return nil
}

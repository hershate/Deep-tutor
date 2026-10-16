package control

import (
	"encoding/json"
	"log/slog"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/state/sessioninbox"
)

func surfaceActionKey(action surfacespec.Action) string {
	return "surface:" + action.CallID + ":" + action.RequestID
}

func (c *Controller) reconcileSurfaceAction(record SurfaceActionRecord, spec *surfacespec.Spec) (SurfaceActionRecord, error) {
	store, err := c.ensureInbox()
	if err != nil {
		return record, err
	}
	for _, meta := range store.Snapshot().Items {
		_, env, readErr := store.ReadItem(meta.ID)
		if readErr != nil {
			return record, readErr
		}
		var action surfacespec.Action
		if json.Unmarshal([]byte(env.Extra[surfacespec.ActionMessageName]), &action) != nil || action.SessionID != c.parentSessionID() || action.CallID != record.Action.CallID || action.SurfaceID != spec.ID() {
			continue
		}
		if action.ValidateIdentity() != nil {
			continue
		}
		if _, valueErr := spec.ActionValues(action.ActionID, action.Values); valueErr != nil {
			continue
		}
		record = SurfaceActionRecord{Action: action, ItemID: meta.ID, Status: "accepted"}
	}
	if record.Status != "accepted" {
		return record, nil
	}
	meta, _, readErr := store.ReadItem(record.ItemID)
	if readErr == nil {
		switch meta.State {
		case sessioninbox.StateQueued, sessioninbox.StateBlocked, sessioninbox.StateUncertain, sessioninbox.StateSteerAccepted:
			record.CanWithdraw = true
		}
		return record, nil
	}
	disposition, found, receiptErr := store.ReceiptDisposition(surfaceActionKey(record.Action))
	if receiptErr != nil {
		return record, receiptErr
	}
	if found && (disposition == sessioninbox.DispositionDeleted || disposition == sessioninbox.DispositionCancelled) {
		record.Status = "withdrawn"
	}
	return record, nil
}

func (c *Controller) saveSurfaceActionReceipt() {
	if _, err := c.snapshotWithDurability(false, false, false); err != nil {
		slog.Warn("controller: save surface action receipt", "err", err)
	}
}

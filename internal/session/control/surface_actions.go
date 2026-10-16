package control

import (
	"encoding/json"
	"reasonix/internal/base/secrets"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/state/sessioninbox"
)

type SurfaceActionRequest struct {
	Action   surfacespec.Action `json:"action"`
	Delivery string             `json:"delivery"`
}

type SurfaceActionRecord struct {
	Active      bool               `json:"active,omitempty"`
	CanWithdraw bool               `json:"canWithdraw,omitempty"`
	Action      surfacespec.Action `json:"action"`
	ItemID      string             `json:"itemId,omitempty"`
	Status      string             `json:"status,omitempty"`
}

type SurfaceActions interface {
	SurfaceActionState(callID string) (SurfaceActionRecord, error)
	SubmitSurfaceAction(req SurfaceActionRequest, via *provider.Via) (sessioninbox.InboxReceipt, error)
}

func (c *Controller) actionSpec(callID string) (*surfacespec.Spec, error) {
	if !c.NativeSurfaces() || c.executor == nil {
		return nil, surfacespec.ErrExpired
	}
	history := c.History()
	var spec *surfacespec.Spec
	complete := false
	matches := 0
	for _, message := range history {
		for _, call := range message.ToolCalls {
			if call.ID == callID {
				matches++
				spec, _ = surfacespec.FromCall(call.Name, call.ResolvedName, call.Arguments)
			}
		}
		if message.Role == provider.RoleTool && message.ToolCallID == callID {
			var result struct {
				Nonblocking bool `json:"nonblocking"`
			}
			complete = message.ToolFailure == nil && !message.LocalOnly && json.Unmarshal([]byte(message.Content), &result) == nil && result.Nonblocking
		}
	}
	if matches != 1 || spec == nil || len(spec.Actions()) == 0 || !complete {
		return nil, surfacespec.ErrExpired
	}
	spec.Redact(secrets.RedactCredentials)
	if err := spec.Validate(); err != nil {
		return nil, err
	}
	return spec, nil
}

func (c *Controller) SurfaceActionState(callID string) (SurfaceActionRecord, error) {
	c.snapshotMu.Lock()
	defer c.snapshotMu.Unlock()
	spec, err := c.actionSpec(callID)
	if err != nil {
		return SurfaceActionRecord{}, err
	}
	record := c.latestSurfaceAction(callID, spec.ID())
	if record.Status == "" {
		record.Action.SurfaceID = spec.ID()
	}
	record, err = c.reconcileSurfaceAction(record, spec)
	if err != nil {
		return record, err
	}
	record.Active = c.surfacePublished(callID, spec.ID())
	if record.Status == "" && !record.Active {
		return SurfaceActionRecord{}, surfacespec.ErrExpired
	}
	return record, nil
}

func (c *Controller) latestSurfaceAction(callID, surfaceID string) SurfaceActionRecord {
	result := SurfaceActionRecord{Action: surfacespec.Action{SurfaceID: surfaceID, SessionID: c.parentSessionID(), CallID: callID}}
	for _, message := range c.History() {
		if !message.LocalOnly || message.Name != surfacespec.ActionMessageName {
			continue
		}
		var record SurfaceActionRecord
		if json.Unmarshal([]byte(message.Content), &record) == nil && record.Action.CallID == callID && record.Action.SurfaceID == surfaceID {
			result = record
		}
	}
	return result
}

func (c *Controller) SubmitSurfaceAction(req SurfaceActionRequest, via *provider.Via) (sessioninbox.InboxReceipt, error) {
	c.snapshotMu.Lock()
	defer c.snapshotMu.Unlock()
	if req.Action.SessionID != c.parentSessionID() {
		return sessioninbox.InboxReceipt{}, surfacespec.ErrExpired
	}
	if err := req.Action.ValidateIdentity(); err != nil {
		return sessioninbox.InboxReceipt{}, err
	}
	if req.Delivery != "queue" && req.Delivery != "steer" {
		return sessioninbox.InboxReceipt{}, surfacespec.ErrValueInvalid
	}
	spec, err := c.actionSpec(req.Action.CallID)
	if err != nil {
		return sessioninbox.InboxReceipt{}, err
	}
	if req.Action.SurfaceID != spec.ID() {
		return sessioninbox.InboxReceipt{}, surfacespec.ErrExpired
	}
	previous, err := c.reconcileSurfaceAction(c.latestSurfaceAction(req.Action.CallID, req.Action.SurfaceID), spec)
	if err != nil {
		return sessioninbox.InboxReceipt{}, err
	}
	if !c.surfacePublished(req.Action.CallID, req.Action.SurfaceID) && previous.Action.RequestID != req.Action.RequestID {
		return sessioninbox.InboxReceipt{}, surfacespec.ErrExpired
	}
	if previous.Status == "accepted" && previous.Action.RequestID != req.Action.RequestID {
		return sessioninbox.InboxReceipt{}, surfacespec.ErrExpired
	}
	values, err := spec.ActionValues(req.Action.ActionID, req.Action.Values)
	if err != nil {
		return sessioninbox.InboxReceipt{}, err
	}
	req.Action.Values, _ = json.Marshal(values)
	if previous.Action.RequestID == req.Action.RequestID {
		before, _ := json.Marshal(previous.Action)
		after, _ := json.Marshal(req.Action)
		if string(before) != string(after) {
			return sessioninbox.InboxReceipt{}, sessioninbox.ErrIdempotencyConflict
		}
		if _, _, readErr := c.ReadInboxItem(previous.ItemID); readErr != nil {
			return sessioninbox.InboxReceipt{ItemID: previous.ItemID, Idempotent: true, Disposition: sessioninbox.DispositionIdempotentHit}, nil
		}
	}
	note, err := req.Action.Note()
	if err != nil {
		return sessioninbox.InboxReceipt{}, err
	}
	actionJSON, _ := json.Marshal(req.Action)
	display := spec.Title
	for _, action := range spec.Actions() {
		if action.ActionID == req.Action.ActionID {
			display = action.Label
			break
		}
	}
	rec, err := c.EnqueueInbox(InboxRequest{Display: display, Raw: display, Submit: note, Source: "http", Via: via, Idempotency: surfaceActionKey(req.Action), SurfaceAction: &req.Action, Extra: map[string]string{surfacespec.ActionMessageName: string(actionJSON), "surface_delivery": req.Delivery}})
	if err != nil {
		return rec, err
	}
	if err = c.recordSurfaceAction(SurfaceActionRecord{Action: req.Action, ItemID: rec.ItemID, Status: "accepted"}); err != nil {
		return rec, err
	}
	c.saveSurfaceActionReceipt()
	if rec.Idempotent {
		if _, _, err = c.ReadInboxItem(rec.ItemID); err != nil {
			return rec, nil
		}
	}
	if req.Delivery == "steer" {
		return c.steerInboxItem(rec.ItemID, true)
	}
	if !c.Running() {
		c.maybeDispatchInbox()
	}
	return rec, nil
}

func (c *Controller) recordSurfaceAction(record SurfaceActionRecord) error {
	raw, err := json.Marshal(record)
	if err != nil {
		return err
	}
	previous := c.latestSurfaceAction(record.Action.CallID, record.Action.SurfaceID)
	encoded, _ := json.Marshal(previous)
	if string(encoded) == string(raw) {
		return nil
	}
	c.executor.Session().Add(provider.Message{Role: provider.RoleUser, LocalOnly: true, Name: surfacespec.ActionMessageName, Content: string(raw)})
	return nil
}

func (c *Controller) surfaceActionWithdrawn(itemID string) error {
	for _, message := range c.History() {
		if !message.LocalOnly || message.Name != surfacespec.ActionMessageName {
			continue
		}
		var record SurfaceActionRecord
		if json.Unmarshal([]byte(message.Content), &record) != nil || record.ItemID != itemID || record.Status != "accepted" {
			continue
		}
		record.Status = "withdrawn"
		if err := c.recordSurfaceAction(record); err != nil {
			return err
		}
		c.saveSurfaceActionReceipt()
		return nil
	}
	return nil
}

package control

import "reasonix/internal/state/checkpoint"

// AvailableUndo is a read: it neither takes the rotation gate nor scans files.
func (c *Controller) AvailableUndo() (*checkpoint.RewindUndo, error) {
	if c.Running() {
		return nil, nil
	}
	return c.checkpoints.storeRef().AvailableUndo()
}

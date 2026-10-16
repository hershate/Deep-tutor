package checkpoint

import (
	"errors"
	"fmt"
	"os"
	"time"
)

// ErrUndoRecoveryPending blocks new mutations until compensation completes.
var ErrUndoRecoveryPending = errors.New("rewind recovery is pending")

type undoState struct {
	current *TransactionManifest
	pending *TransactionManifest
}

func (s *Store) requireUndoRecoveryComplete() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.undo.pending != nil {
		return ErrUndoRecoveryPending
	}
	return nil
}

func (s *Store) trackUndoTransaction(tx *TransactionManifest) (func(), error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.undo.pending != nil {
		return nil, ErrUndoRecoveryPending
	}
	previous := s.undoOfferLocked()
	cp := *tx
	cp.State = TxCommitting
	s.undo.pending = &cp
	return func() {
		s.mu.Lock()
		defer s.unlockUndoChange(previous)
		if tx.State == TxCommitted || tx.State == TxAborted {
			s.undo.pending = nil
		} else {
			cp := *tx
			s.undo.pending = &cp
		}
	}, nil
}

// InvalidateUndo persists retirement of the undo offer before new activity.
func (s *Store) InvalidateUndo() error {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.unlockUndoChange(s.undoOfferLocked())
	return s.invalidateUndoLocked()
}

func (s *Store) invalidateUndoLocked() error {
	if s.undo.pending != nil {
		return ErrUndoRecoveryPending
	}
	if s.undo.current == nil {
		return nil
	}
	tx := *s.undo.current
	tx.State = TxInvalidated
	tx.UpdatedAt = time.Now()
	if err := s.persistTransaction(&tx); err != nil {
		return fmt.Errorf("persist undo invalidation %s: %w", tx.ID, err)
	}
	s.undo.current = nil
	return nil
}

func (s *Store) undoFileConflicts(tx *TransactionManifest) ([]RewindConflict, error) {
	for _, t := range tx.Targets {
		fp, err := FingerprintPath(s.root, t.AbsPath)
		if err != nil && !os.IsNotExist(err) {
			return nil, fmt.Errorf("fingerprint %s before undo: %w", t.Path, err)
		}
		// After commit, disk should match restore image. If it doesn't, refuse.
		if t.Action == "delete" {
			if fp.Existed {
				return []RewindConflict{{
					Path: t.Path, Reason: ConflictManualEdit, CurrentSHA: fp.SHA256,
				}}, nil
			}
		} else {
			if !fingerprintMatches(fp, t.RestoreExisted, t.RestoreSHA, t.RestoreMode) {
				restoreExisted := t.RestoreExisted
				return []RewindConflict{{
					Path: t.Path, Reason: CompareIdentity(fp, t.RestoreSHA, &restoreExisted, t.RestoreMode),
					CurrentSHA: fp.SHA256, LastOwnedSHA: t.RestoreSHA, CurrentMode: fp.Mode, CheckpointMode: t.RestoreMode,
				}}, nil
			}
		}
	}
	return nil, nil
}

func (s *Store) restoreUndoSlot(latest *TransactionManifest, undoneParents map[string]bool, recovering []*TransactionManifest) {
	s.mu.Lock()
	defer s.unlockUndoChange(s.undoOfferLocked())
	s.undo = undoState{}
	for _, tx := range recovering {
		if tx.State == TxCommitting {
			cp := *tx
			s.undo.pending = &cp
			break
		}
	}
	if latest != nil && latest.State == TxCommitted && !undoneParents[latest.ID] {
		s.undo.current = latest
	}
}

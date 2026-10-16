package checkpoint

// OnUndoChange installs the owner's content-free notification callback.
func (s *Store) OnUndoChange(notify func()) {
	s.mu.Lock()
	s.undoChanged = notify
	s.mu.Unlock()
}

// AvailableUndo reads the saved offer without disk I/O or file verification.
func (s *Store) AvailableUndo() (*RewindUndo, error) {
	if s == nil || s.barrier.HasWriters() {
		return nil, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	tx := s.undoOfferLocked()
	if tx == nil || len(s.activeWriters) > 0 {
		return nil, nil
	}
	return &RewindUndo{TransactionID: tx.ID, Turn: tx.Turn, Files: len(tx.Targets)}, nil
}

// Callbacks may read the offer, so release the lock before notifying.
func (s *Store) unlockUndoChange(previous *TransactionManifest) {
	before, after := "", ""
	if previous != nil {
		before = previous.ID
	}
	if offer := s.undoOfferLocked(); offer != nil {
		after = offer.ID
	}
	notify := s.undoChanged
	s.mu.Unlock()
	if before != after && notify != nil {
		notify()
	}
}

// LastUndoTransactionID returns the committed transaction id available for undo.
func (s *Store) LastUndoTransactionID() string {
	if s == nil {
		return ""
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if offer := s.undoOfferLocked(); offer != nil {
		return offer.ID
	}
	return ""
}

func (s *Store) undoOfferLocked() *TransactionManifest {
	if s.undo.pending != nil || s.undo.current == nil || s.undo.current.State != TxCommitted {
		return nil
	}
	return s.undo.current
}

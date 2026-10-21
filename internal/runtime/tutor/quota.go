package tutor

import (
	"errors"
	"fmt"
	"strings"
	"sync"
)

// quota.go — the file store's stand-in for the database's transaction.
//
// A count-then-write pair has a window: two writers that both count before
// either writes both pass the check, so the cap is exceeded by the number of
// concurrent writers. The tutor store has no transaction, so the count and
// the write share one keyed lock — the discipline the Python backend uses
// (backend/app/core/quota.py). The lock lives on the store because the store
// owns the files: the service, the package-level import and the agent tool
// all write through the same store and must serialise with each other.

// The store's live caps. The import path used to carry its own copy of a few
// of them, which is how the two drifted apart: the caps were enforced where
// the backup restored a document and nowhere else.
const (
	maxSessions            = 200
	maxMaterialsPerSession = 20
	maxPlansPerSession     = 50
	// A check-in's own bounds: the ledger keeps every entry in one file, so
	// an unbounded note or minutes would make it unreadable.
	maxLogMinutes   = 1440
	maxLogNoteRunes = 2000
)

// ErrBadLogInput is a malformed check-in.
var ErrBadLogInput = errors.New("tutor: invalid learning log")

// keyedLocks hands out one lock per key and forgets a key once nobody holds
// it, so the map does not grow with the number of resources.
type keyedLocks struct {
	mu    sync.Mutex
	locks map[string]*keyedLock
}

type keyedLock struct {
	mu   sync.Mutex
	refs int
}

// Lock takes the lock for key and returns the function that releases it.
func (k *keyedLocks) Lock(key string) func() {
	k.mu.Lock()
	if k.locks == nil {
		k.locks = make(map[string]*keyedLock)
	}
	l := k.locks[key]
	if l == nil {
		l = &keyedLock{}
		k.locks[key] = l
	}
	l.refs++
	k.mu.Unlock()

	l.mu.Lock()
	return func() {
		l.mu.Unlock()
		k.mu.Lock()
		l.refs--
		if l.refs == 0 {
			delete(k.locks, key)
		}
		k.mu.Unlock()
	}
}

// quotaKey is the lock key of a resource family.
func quotaKey(kind string) string { return kind }

// ErrNoteQuota is the notes cap.
var ErrNoteQuota = errors.New("tutor: note quota exceeded")

// ErrSessionQuota is the sessions cap.
var ErrSessionQuota = errors.New("tutor: session quota exceeded")

// ErrMaterialQuota is the per-session materials cap.
var ErrMaterialQuota = errors.New("tutor: material quota exceeded")

// planKey is the lock key of one plan's derived files (attempts, progress,
// logs): they are read-modify-written, so two writers would lose one of the
// updates.
func planKey(planID string) string { return "plan:" + planID }

// materialsKey is the lock key of one session's materials: the count and the
// row write share it, so concurrent uploads cannot both pass the check.
func materialsKey(sessionID string) string { return "materials:" + sessionID }

// kbKey is the lock key of the knowledge-base library: the dedup check, the
// document-count check and the row write share it.
const kbKey = "kb"

// WithPlanLock runs fn while holding the plan's lock.
func (s *Store) WithPlanLock(planID string, fn func() error) error {
	unlock := s.quota.Lock(planKey(planID))
	defer unlock()
	return fn()
}

// WithKbLock runs fn while holding the library's lock.
func (s *Store) WithKbLock(fn func() error) error {
	unlock := s.quota.Lock(kbKey)
	defer unlock()
	return fn()
}

// WithMaterialsLock runs fn while holding one session's materials lock.
func (s *Store) WithMaterialsLock(sessionID string, fn func() error) error {
	unlock := s.quota.Lock(materialsKey(sessionID))
	defer unlock()
	return fn()
}

// AddMaterialGuarded adds a material under the per-session cap: the count and
// the row write share one lock, so two concurrent uploads cannot both pass
// the check. The cap existed as a constant but nothing enforced it — the
// materials directory of a session grew without bound.
func (s *Store) AddMaterialGuarded(m *Material) error {
	unlock := s.quota.Lock(materialsKey(m.SessionID))
	defer unlock()
	existing, err := s.Materials(m.SessionID)
	if err != nil {
		return err
	}
	if len(existing) >= maxMaterialsPerSession {
		return fmt.Errorf("%w: at most %d materials per session", ErrMaterialQuota, maxMaterialsPerSession)
	}
	return s.SaveMaterial(m)
}

// SaveNoteGuarded writes a note under the notes quota: the count and the
// write share one lock, so two concurrent writers cannot both pass the
// check. A note that already exists (an edit) is not counted against the
// quota. The title and the body are clamped here, so every write path —
// the API, the import, the backup restore and the agent's promote tool —
// stores the same bounded shape.
func (s *Store) SaveNoteGuarded(n *Note) error {
	unlock := s.quota.Lock(quotaKey("note"))
	defer unlock()
	if n.ID == "" {
		notes, err := s.Notes()
		if err != nil {
			return err
		}
		if len(notes) >= noteQuota {
			return fmt.Errorf("%w: at most %d notes", ErrNoteQuota, noteQuota)
		}
	}
	n.Title = clampRunes(strings.TrimSpace(n.Title), noteTitleMax)
	n.Body = clampRunes(n.Body, noteBodyMax)
	return s.SaveNote(n)
}

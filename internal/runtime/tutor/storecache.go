package tutor

import (
	"errors"
	"os"
	"path/filepath"
	"sync"
)

// storecache.go backs the store's list paths with stat-validated decodes:
// every read stats the file first, and a matching size+mtime reuses the
// previous decode. Writes publish by atomic rename, which always moves mtime.

// fileStamp is the cheap identity of one file's current bytes.
type fileStamp struct {
	Size  int64 `json:"size"`
	MTime int64 `json:"mtime"`
}

func stampOf(info os.FileInfo) fileStamp {
	return fileStamp{Size: info.Size(), MTime: info.ModTime().UnixNano()}
}

// stamped pairs a decoded value with the stamp it was decoded from.
type stamped[T any] struct {
	stamp fileStamp
	val   T
}

// storeCache holds stat-validated decodes for the list paths. Entries for
// files that later disappear linger — each is small and keyed by an id that
// existed. A nil map is a permanent miss, so a zero-value Store stays usable.
type storeCache struct {
	mu       sync.Mutex
	sessions map[string]stamped[*GenSession]
	notes    map[string]stamped[Note]
	nodes    map[string]stamped[[]planNodeRef]

	plansLoaded bool
	plansIdx    stamped[[]PlanSummary]
}

// cachedSession returns the session under one id, reusing the previous
// decode when the file's stamp is unchanged. The caller gets its own copy
// either way, so mutating a returned session cannot touch the cache.
func (s *Store) cachedSession(id string) *GenSession {
	path := filepath.Join(s.sessionDir(id), "session.json")
	info, err := os.Stat(path)
	if err != nil {
		return nil
	}
	stamp := stampOf(info)
	s.cache.mu.Lock()
	c, ok := s.cache.sessions[id]
	s.cache.mu.Unlock()
	if ok && c.stamp == stamp {
		return cloneSession(c.val)
	}
	var sess GenSession
	if err := s.readJSON(path, &sess); err != nil {
		return nil
	}
	s.cache.mu.Lock()
	if s.cache.sessions == nil {
		s.cache.sessions = make(map[string]stamped[*GenSession])
	}
	s.cache.sessions[id] = stamped[*GenSession]{stamp, cloneSession(&sess)}
	s.cache.mu.Unlock()
	return &sess
}

func cloneSession(sess *GenSession) *GenSession {
	cp := *sess
	cp.ClarifyQuestions = append([]string(nil), sess.ClarifyQuestions...)
	return &cp
}

// cachedNote mirrors cachedSession with the stamp supplied by the caller:
// on Windows the directory enumeration Notes() already ran carries each
// file's size+mtime, so a warm listing costs no per-file syscall. Note is
// flat values, so the cached entry needs no defensive copy.
func (s *Store) cachedNote(name string, info os.FileInfo) *Note {
	path := filepath.Join(s.notesDir(), name)
	stamp := stampOf(info)
	s.cache.mu.Lock()
	c, ok := s.cache.notes[name]
	s.cache.mu.Unlock()
	if ok && c.stamp == stamp {
		note := c.val
		return &note
	}
	var note Note
	if err := s.readJSON(path, &note); err != nil {
		return nil
	}
	s.cache.mu.Lock()
	if s.cache.notes == nil {
		s.cache.notes = make(map[string]stamped[Note])
	}
	s.cache.notes[name] = stamped[Note]{stamp, note}
	s.cache.mu.Unlock()
	return &note
}

// cachedPlans returns the plan summaries from index.json behind the same
// stamp check; the caller gets its own slice to sort.
func (s *Store) cachedPlans() ([]PlanSummary, error) {
	path := filepath.Join(s.root, "plans", "index.json")
	info, err := os.Stat(path)
	if err != nil {
		return s.readPlansIndex(path)
	}
	stamp := stampOf(info)
	s.cache.mu.Lock()
	hit := s.cache.plansLoaded && s.cache.plansIdx.stamp == stamp
	var out []PlanSummary
	if hit {
		out = append([]PlanSummary(nil), s.cache.plansIdx.val...)
	}
	s.cache.mu.Unlock()
	if hit {
		return out, nil
	}
	plans, _, err := s.readPlansIndexRaw(path)
	if err != nil {
		return nil, err
	}
	s.cache.mu.Lock()
	s.cache.plansLoaded = true
	s.cache.plansIdx = stamped[[]PlanSummary]{stamp, plans}
	s.cache.mu.Unlock()
	return append([]PlanSummary(nil), plans...), nil
}

// readPlansIndex is the uncached read, also used when the stat itself fails
// so error reporting stays identical.
func (s *Store) readPlansIndex(path string) ([]PlanSummary, error) {
	plans, _, err := s.readPlansIndexRaw(path)
	return plans, err
}

func (s *Store) readPlansIndexRaw(path string) ([]PlanSummary, planIndex, error) {
	var idx planIndex
	if err := s.readJSON(path, &idx); err != nil {
		if errors.Is(err, ErrNotFound) {
			return nil, idx, nil
		}
		return nil, idx, err
	}
	return idx.Plans, idx, nil
}

// cachedNodeNames returns a plan's knowledge node list through the same
// stamp check; the returned slice is a fresh copy.
func (s *Store) cachedNodeNames(id string) ([]planNodeRef, error) {
	path := filepath.Join(s.planDir(id), "plan.json")
	info, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	stamp := stampOf(info)
	s.cache.mu.Lock()
	c, ok := s.cache.nodes[id]
	s.cache.mu.Unlock()
	if ok && c.stamp == stamp {
		return append([]planNodeRef(nil), c.val...), nil
	}
	var lean struct {
		Knowledge struct {
			Nodes []planNodeRef `json:"nodes"`
		} `json:"knowledge_map"`
	}
	if err := s.readJSON(path, &lean); err != nil {
		return nil, err
	}
	s.cache.mu.Lock()
	if s.cache.nodes == nil {
		s.cache.nodes = make(map[string]stamped[[]planNodeRef])
	}
	s.cache.nodes[id] = stamped[[]planNodeRef]{stamp, lean.Knowledge.Nodes}
	s.cache.mu.Unlock()
	return append([]planNodeRef(nil), lean.Knowledge.Nodes...), nil
}

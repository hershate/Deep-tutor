package trustedstate

import (
	"io/fs"
	"time"
)

const (
	// A modification time says nothing about a rewrite that lands inside the
	// file system's timestamp granularity. A time with no sub-second part is
	// taken to come from a coarse clock and gets the long window.
	racyWindow       = 20 * time.Millisecond
	coarseRacyWindow = 2 * time.Second
	maxCachedObject  = 1 << 20
	maxCachedBytes   = 16 << 20
	maxCachedCount   = 1024
)

type cachedObject struct {
	data []byte
	size int64
	mod  time.Time
	id   fileID
	read time.Time
}

type objectCache struct {
	byPath map[string]cachedObject
	bytes  int
}

func (s *Store) rememberObject(path string, data []byte, info fs.FileInfo) {
	if len(data) > maxCachedObject || info.Size() != int64(len(data)) {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.objects.byPath == nil || len(s.objects.byPath) >= maxCachedCount || s.objects.bytes+len(data) > maxCachedBytes {
		s.objects = objectCache{byPath: map[string]cachedObject{}}
	}
	s.objects.byPath[path] = cachedObject{data: append([]byte(nil), data...), size: info.Size(), mod: info.ModTime(), id: fileIdentity(info), read: s.now()}
	s.objects.bytes += len(data)
}

// cachedObject answers from memory only while the file still shows the size,
// modification time and identity (inode, change time) it had when its bytes
// were verified, and only when that
// modification was old enough for a later one to have changed the time.
func (s *Store) cachedObject(path string) ([]byte, bool) {
	s.mu.Lock()
	entry, ok := s.objects.byPath[path]
	s.mu.Unlock()
	if !ok || entry.mod.After(entry.read.Add(-entry.window())) {
		return nil, false
	}
	info, err := s.statFile(path)
	if err != nil || info.Size() != entry.size || !info.ModTime().Equal(entry.mod) || fileIdentity(info) != entry.id {
		return nil, false
	}
	return append([]byte(nil), entry.data...), true
}

func (c cachedObject) window() time.Duration {
	if c.mod.Nanosecond() == 0 {
		return coarseRacyWindow
	}
	return racyWindow
}

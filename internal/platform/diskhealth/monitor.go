// Package diskhealth decides when the volume a workspace or the runtime's state
// lives on is short enough of space to be worth saying so. A build can fill a
// disk in minutes and the agent then fails on writes with messages that never
// name the cause; the monitor is the rule for when to speak, not a cleaner.
package diskhealth

import (
	"strconv"
	"sync"
	"time"

	"reasonix/internal/state/storage"
)

const (
	// LowFreeBytes and LowFreePercent are the two ways to be short: little room in
	// absolute terms, or little relative to a large volume.
	LowFreeBytes   = int64(5) << 30
	LowFreePercent = 3
	// QuietFor is how long a volume that stays low is left alone after it was
	// reported, so a disk that never recovers is a reminder, not a stream.
	QuietFor = 6 * time.Hour
)

// Volume is what a filesystem says it has: free bytes a process can use, and the total.
type Volume struct {
	Free, Total int64
}

// Notice is one volume that has become short of space, named by a folder on it.
type Notice struct {
	Dir         string
	Free, Total int64
}

// Low reports whether the volume is short of space. A volume that does not say
// how big it is is never low: nothing can be concluded from it.
func Low(v Volume) bool {
	return v.Total > 0 && (v.Free < LowFreeBytes || v.Free*100 < v.Total*LowFreePercent)
}

// recovered is clearly out of the shortage, with margin, so a volume hovering at
// the line is not reported again each time it crosses.
func recovered(v Volume) bool {
	return v.Total > 0 && v.Free >= LowFreeBytes*3/2 && v.Free*1000 >= v.Total*LowFreePercent*15
}

// Monitor remembers which volumes it has already spoken about.
type Monitor struct {
	mu     sync.Mutex
	raised map[string]time.Time
}

func NewMonitor() *Monitor { return &Monitor{raised: map[string]time.Time{}} }

// Check reads each folder's volume and returns the notices that are due: a
// volume that became low, or stayed low through the quiet period. Folders on
// one volume are one notice, and a folder whose volume cannot be read is left
// out rather than guessed at.
func (m *Monitor) Check(now time.Time, dirs []string, read func(dir string) (Volume, bool)) []Notice {
	m.mu.Lock()
	defer m.mu.Unlock()
	var due []Notice
	seen := map[string]bool{}
	for _, dir := range dirs {
		v, ok := read(dir)
		if !ok || v.Total <= 0 {
			continue
		}
		// The same total and the same free space to the megabyte is the same
		// volume as far as anything here can tell.
		key := strconv.FormatInt(v.Total, 10) + ":" + strconv.FormatInt(v.Free>>20, 10)
		if seen[key] {
			continue
		}
		seen[key] = true
		id := dir
		at, was := m.raised[id]
		switch {
		case Low(v) && (!was || now.Sub(at) >= QuietFor):
			m.raised[id] = now
			due = append(due, Notice{Dir: dir, Free: v.Free, Total: v.Total})
		case recovered(v):
			delete(m.raised, id)
		}
	}
	return due
}

// Read asks the filesystem holding dir how much room it has; ok is false when it would not say.
func Read(dir string) (Volume, bool) {
	v := storage.VolumeOf(dir)
	return Volume{Free: v.Free, Total: v.Total}, v.Total > 0
}

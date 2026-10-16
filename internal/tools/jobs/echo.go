package jobs

import (
	"bytes"
	"sync"
	"unicode/utf8"
)

const (
	// echoLimit caps one wait's live echo, as a shell caps its live stream; the
	// wait's result still carries the job's output in full.
	echoLimit  = 64 << 10
	echoCapped = "\n...[live output capped at 64 KiB; the result shows the rest when the wait ends]...\n"
	// echoSeed is how much already-written output a wait shows when it begins,
	// so its row opens on the job's latest lines rather than on nothing.
	echoSeed = 4 << 10
	// echoOpenLimit is how much of a line with no newline yet is held back; a
	// progress bar or a binary stream is shown in pieces of this size instead.
	echoOpenLimit = 4 << 10
)

// echo forwards what waited-on jobs write as whole lines. Jobs append under
// their own lock, so nothing here calls out: a pump goroutine does that.
type echo struct {
	emit   func(string)
	named  bool
	wake   chan struct{}
	mu     sync.Mutex
	queued []byte
	open   map[string][]byte
	sent   int
	capped bool
}

func newEcho(emit func(string), named bool) *echo {
	return &echo{emit: emit, named: named, wake: make(chan struct{}, 1), open: map[string][]byte{}}
}

// take receives one job's bytes; called under that job's lock.
func (e *echo) take(id string, p []byte) {
	e.mu.Lock()
	defer e.mu.Unlock()
	buf := append(e.open[id], p...)
	var ready []byte
	if end := bytes.LastIndexByte(p, '\n'); end >= 0 {
		cut := len(buf) - len(p) + end + 1
		ready, buf = buf[:cut], buf[cut:]
	}
	if len(buf) > echoOpenLimit {
		piece := completeRunes(buf)
		ready = append(append(append([]byte(nil), ready...), piece...), '\n')
		buf = buf[len(piece):]
	}
	e.open[id] = append([]byte(nil), buf...)
	if len(ready) == 0 {
		return
	}
	e.queueLocked(id, ready)
	select {
	case e.wake <- struct{}{}:
	default:
	}
}

func (e *echo) queueLocked(id string, lines []byte) {
	if e.capped || len(lines) == 0 {
		return
	}
	if e.named {
		var b bytes.Buffer
		for line := range bytes.Lines(lines) {
			b.WriteString("[" + id + "] ")
			b.Write(line)
		}
		lines = b.Bytes()
	}
	room := echoLimit - e.sent
	if len(lines) > room {
		cut := bytes.LastIndexByte(lines[:room], '\n') + 1
		e.queued = append(e.queued, lines[:cut]...)
		e.queued = append(e.queued, echoCapped...)
		e.sent = echoLimit
		e.capped = true
		return
	}
	e.queued = append(e.queued, lines...)
	e.sent += len(lines)
}

// flush hands what is queued to emit, outside every lock but the caller's.
func (e *echo) flush() {
	e.mu.Lock()
	out := e.queued
	e.queued = nil
	e.mu.Unlock()
	if len(out) > 0 {
		e.emit(string(out))
	}
}

// finish releases each job's unterminated last line, up to the last whole
// character, and then everything still queued.
func (e *echo) finish(order []*Job) {
	e.mu.Lock()
	for _, j := range order {
		rest := completeRunes(e.open[j.ID])
		if len(rest) > 0 {
			e.queueLocked(j.ID, append(rest, '\n'))
		}
		delete(e.open, j.ID)
	}
	e.mu.Unlock()
	e.flush()
}

// completeRunes drops only a multi-byte character the bytes end inside of;
// invalid bytes elsewhere are the job's output and pass through as they are.
func completeRunes(b []byte) []byte {
	for i := len(b) - 1; i >= 0 && i >= len(b)-utf8.UTFMax; i-- {
		if utf8.RuneStart(b[i]) {
			if !utf8.FullRune(b[i:]) {
				return b[:i]
			}
			break
		}
	}
	return b
}

// echoTo registers e on j and queues the latest output already written, both
// under j's lock so no write falls between them or arrives twice.
func (j *Job) echoTo(e *echo) *outputWatch {
	w := &outputWatch{echo: func(p []byte) { e.take(j.ID, p) }}
	j.mu.Lock()
	defer j.mu.Unlock()
	seed := j.tail
	if len(seed) > echoSeed {
		seed = seed[len(seed)-echoSeed:]
		if i := bytes.IndexByte(seed, '\n'); i >= 0 {
			seed = seed[i+1:]
		}
		for len(seed) > 0 && !utf8.RuneStart(seed[0]) {
			seed = seed[1:]
		}
	}
	if len(seed) > 0 {
		e.take(j.ID, seed)
	}
	j.watches = append(j.watches, w)
	return w
}

// pumpEcho registers e on every target and forwards until stop is called,
// which flushes what remains.
func pumpEcho(e *echo, targets []*Job) (stop func()) {
	watches := make([]*outputWatch, len(targets))
	for i, j := range targets {
		watches[i] = j.echoTo(e)
	}
	done, exited := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(exited)
		for {
			select {
			case <-e.wake:
				e.flush()
			case <-done:
				return
			}
		}
	}()
	return func() {
		for i, j := range targets {
			j.unwatch(watches[i])
		}
		close(done)
		<-exited
		e.finish(targets)
	}
}

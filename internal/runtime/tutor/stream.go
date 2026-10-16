package tutor

import (
	"encoding/json"
	"sync"
)

// Wire event names, Deep-tutor's SSE vocabulary.
const (
	EventToolStart      = "tool_start"
	EventToolEnd        = "tool_end"
	EventClarifyNeeded  = "clarify_needed"
	EventWarn           = "warn"
	EventPlanChunk      = "plan_chunk"
	EventBudgetExceeded = "budget_exceeded"
	EventError          = "error"
	EventDone           = "done"

	maxBufferedEvents  = 500
	subscriberQueueMax = 512
	terminalRetireSec  = 300
)

// StreamEvent is one numbered SSE frame: the id is the resume cursor a
// reconnecting client replays from.
type StreamEvent struct {
	ID   int64           `json:"id"`
	Type string          `json:"type"`
	Data json.RawMessage `json:"data"`
}

// sessionStream is the per-generation event hub: a bounded history every late
// subscriber replays from, and a fan-out to live subscribers whose queues are
// bounded too. A client that falls behind is kicked rather than blocking the
// run; its reconnect replays what its last id names.
type sessionStream struct {
	mu       sync.Mutex
	seq      int64
	events   []StreamEvent
	subs     map[*streamSubscriber]struct{}
	terminal *StreamEvent // survives the history rotating, for late replays
}

func newSessionStream() *sessionStream {
	return &sessionStream{subs: map[*streamSubscriber]struct{}{}}
}

// publish numbers, buffers, and fans out one event, returning its id.
func (s *sessionStream) publish(typ string, data any) int64 {
	raw, err := json.Marshal(data)
	if err != nil {
		raw = []byte(`{}`)
	}
	s.mu.Lock()
	s.seq++
	ev := StreamEvent{ID: s.seq, Type: typ, Data: raw}
	s.events = append(s.events, ev)
	if len(s.events) > maxBufferedEvents {
		s.events = s.events[len(s.events)-maxBufferedEvents:]
	}
	for sub := range s.subs {
		sub.push(ev)
	}
	s.mu.Unlock()
	return ev.ID
}

// markTerminal records the run's terminal event. Later subscribers still get
// the full replay; this copy exists for after the history rotates.
func (s *sessionStream) markTerminal(id int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.terminal != nil {
		return
	}
	for _, ev := range s.events {
		if ev.ID == id {
			cp := ev
			s.terminal = &cp
			return
		}
	}
}

// subscribe registers a live queue seeded with everything numbered after
// lastID, so replay precedes every live event by construction. The channel
// closes on kick, unsubscribe, or retire; ErrKicked reports a subscriber the
// fan-out dropped, which a client answers by reconnecting with its last id.
type kickedError struct{}

func (kickedError) Error() string { return "tutor: stream subscriber kicked" }

// ErrKicked is reported by a subscription the fan-out dropped for lagging;
// the client reconnects with its last event id and replays the difference.
var ErrKicked error = kickedError{}

func (s *sessionStream) subscribe(lastID int64) (<-chan StreamEvent, func()) {
	sub := newStreamSubscriber()
	s.mu.Lock()
	for _, ev := range s.events {
		if ev.ID > lastID {
			sub.enqueue(ev)
		}
	}
	s.subs[sub] = struct{}{}
	s.mu.Unlock()
	return sub.ch, func() {
		s.mu.Lock()
		delete(s.subs, sub)
		s.mu.Unlock()
		sub.close()
	}
}

// retire closes every subscriber; the buffered history stays readable for the
// service's retirement window.
func (s *sessionStream) retire() {
	s.mu.Lock()
	defer s.mu.Unlock()
	for sub := range s.subs {
		delete(s.subs, sub)
		sub.close()
	}
}

// streamSubscriber is one client's outbound queue, drained by a single pump
// goroutine so publish never blocks on a slow reader. Over budget it drops
// the oldest events and marks itself kicked: the client will notice the gap
// via the id cursor and reconnect rather than be shown a lie.
type streamSubscriber struct {
	ch     chan StreamEvent
	done   chan struct{}
	wake   chan struct{}
	kicked bool

	mu     sync.Mutex
	queue  []StreamEvent
	bytes  int
	closed bool
}

func newStreamSubscriber() *streamSubscriber {
	s := &streamSubscriber{ch: make(chan StreamEvent, 64), done: make(chan struct{}), wake: make(chan struct{}, 1)}
	go s.pump()
	return s
}

func (s *streamSubscriber) enqueue(ev StreamEvent) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return
	}
	s.queue = append(s.queue, ev)
	s.bytes += len(ev.Data)
	for s.bytes > subscriberQueueMax*1024 && len(s.queue) > 1 {
		s.bytes -= len(s.queue[0].Data)
		s.queue = s.queue[1:]
		s.kicked = true
	}
}

func (s *streamSubscriber) push(ev StreamEvent) {
	s.enqueue(ev)
	select {
	case s.wake <- struct{}{}:
	default:
	}
}

func (s *streamSubscriber) pump() {
	defer close(s.ch)
	for {
		s.mu.Lock()
		if len(s.queue) == 0 {
			kicked := s.kicked
			s.mu.Unlock()
			if kicked {
				return
			}
			select {
			case <-s.wake:
				continue
			case <-s.done:
				return
			}
		}
		ev := s.queue[0]
		s.queue[0] = StreamEvent{}
		s.queue = s.queue[1:]
		s.bytes -= len(ev.Data)
		s.mu.Unlock()
		select {
		case s.ch <- ev:
		case <-s.done:
			return
		}
	}
}

func (s *streamSubscriber) close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	s.mu.Unlock()
	close(s.done)
}

package serve

import (
	"encoding/json"
	"net/http"
	"strings"

	"reasonix/internal/contract/event"
)

func (h *Hub) treeChanged() {
	h.changes.Emit(event.Event{Kind: event.Notice, Code: event.NoticeCodeSessionTreeChanged})
}

func (b *Broadcaster) observeTree(fn func()) {
	b.mu.Lock()
	b.onTreeChanged = fn
	b.mu.Unlock()
}

func (b *Broadcaster) treeChanged() {
	if b == nil {
		return
	}
	b.mu.Lock()
	observer := b.onTreeChanged
	b.mu.Unlock()
	if observer != nil {
		observer()
	}
}

func (h *Hub) treeEventsReplay(w http.ResponseWriter, r *http.Request) {
	writeEventsReplay(w, r, h.changes)
}

func (h *Hub) treeEvents(w http.ResponseWriter, r *http.Request) {
	ch, stop := h.changes.SubscribeFrom(lastEventID(r))
	defer stop()
	h.changes.watermarkTo(ch)
	streamFrames(w, r, h.changes, ch)
}

func (b *Broadcaster) watermarkTo(target <-chan Frame) {
	b.mu.Lock()
	defer b.mu.Unlock()
	data, _ := json.Marshal(struct {
		Kind string `json:"kind"`
		Seq  int64  `json:"seq"`
	}{"stream_watermark", b.seq})
	for s := range b.subs {
		if (<-chan Frame)(s.ch) == target {
			s.push(Frame{Data: data}, false)
			return
		}
	}
}

type treeWriteResponse struct {
	http.ResponseWriter
	status int
}

func (w *treeWriteResponse) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
	}
	w.ResponseWriter.WriteHeader(status)
}

func treeWrite(path string) bool {
	if path == "/tree/sessions/export" {
		return false
	}
	if strings.HasPrefix(path, "/tree/") || path == "/runtimes" || strings.HasPrefix(path, "/runtimes/") {
		return true
	}
	if tail, ok := strings.CutPrefix(path, runtimePrefix); ok {
		_, rest, ok := strings.Cut(tail, "/")
		if !ok {
			return false
		}
		path = "/" + rest
	}
	switch path {
	case "/new", "/resume", "/fork", "/rewind", "/summarize", "/delete-session", "/workspace":
		return true
	}
	return false
}

func (h *Hub) watchTreeWrites(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet || r.Method == http.MethodHead || !treeWrite(r.URL.Path) {
			next.ServeHTTP(w, r)
			return
		}
		response := &treeWriteResponse{ResponseWriter: w}
		next.ServeHTTP(response, r)
		if response.status == 0 || response.status >= 200 && response.status < 300 {
			h.treeChanged()
		}
	})
}

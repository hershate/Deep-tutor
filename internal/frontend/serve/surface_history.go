package serve

import (
	"encoding/json"
	"errors"
	"io/fs"
	"net/http"
	"os"
	"sort"
	"strings"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/eventwire"
	"reasonix/internal/state/store"
)

// Persist identity only: plugin values and form defaults never enter the log.
func surfaceMarker(raw []byte) ([]byte, bool) {
	var w eventwire.Event
	if json.Unmarshal(raw, &w) != nil || w.Extension == nil || w.Extension.Kind == event.ExtensionSurfaceNotification {
		return nil, false
	}
	p := w.Extension
	w = eventwire.Event{Kind: w.Kind, Extension: &eventwire.ExtensionSurface{Code: event.SurfaceExpired, PluginID: p.PluginID, SurfaceID: p.SurfaceID, SessionID: p.SessionID, Generation: p.Generation, Kind: p.Kind}}
	raw, err := json.Marshal(w)
	return raw, err == nil
}

// surfaceReplay is what a reopened window gets back: the surfaces the session
// had, and whether it can say that is all of them. A log kept to its cap is
// ordinary; Incomplete is set only when a surface frame was among those dropped.
type surfaceReplay struct {
	Surfaces   []*eventwire.ExtensionSurface `json:"surfaces"`
	Incomplete string                        `json:"incomplete,omitempty"`
}

// replayDropped says a surface frame was refused after the log reached its cap.
const replayDropped = "dropped"

// codeSurfaceReplayUnreadable is a history that could not be read at all.
const codeSurfaceReplayUnreadable = "surface.replay_unreadable"

func (s *Server) extensionSurfaces(w http.ResponseWriter, r *http.Request) {
	s.bindMu.Lock()
	defer s.bindMu.Unlock()
	ctl := s.ctl()
	path := ctl.SessionPath()
	found := map[string]*eventwire.ExtensionSurface{}
	incomplete := ""
	if path != "" {
		s.wire.mu.Lock()
		data, err := os.ReadFile(store.SessionWireLog(path))
		meta, metaErr := readWireLogMeta(store.SessionWireLogMeta(path))
		s.wire.mu.Unlock()
		if err != nil && !errors.Is(err, fs.ErrNotExist) || metaErr != nil {
			refuse(w, http.StatusInternalServerError, codeSurfaceReplayUnreadable, "extension surface history is unreadable", nil)
			return
		}
		if meta.SurfacesDropped {
			incomplete = replayDropped
		}
		for line := range strings.SplitSeq(string(data), "\n") {
			if strings.TrimSpace(line) == "" {
				continue
			}
			var ev eventwire.Event
			if json.Unmarshal([]byte(line), &ev) != nil {
				refuse(w, http.StatusInternalServerError, codeSurfaceReplayUnreadable, "extension surface history is unreadable", nil)
				return
			}
			if ev.Extension == nil || ev.Extension.Kind == event.ExtensionSurfaceNotification {
				continue
			}
			p := ev.Extension
			found[p.PluginID+"\x00"+p.SurfaceID] = &eventwire.ExtensionSurface{Code: event.SurfaceExpired, PluginID: p.PluginID, SurfaceID: p.SurfaceID, SessionID: p.SessionID, Generation: p.Generation, Kind: p.Kind}
		}
	}
	for _, p := range ctl.ExtensionSurfaces() {
		found[p.PluginID+"\x00"+p.SurfaceID] = eventwire.ToWireExtensionSurface(&p)
	}
	keys := make([]string, 0, len(found))
	for key := range found {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	out := make([]*eventwire.ExtensionSurface, 0, len(keys))
	for _, key := range keys {
		out = append(out, found[key])
	}
	writeJSON(w, surfaceReplay{Surfaces: out, Incomplete: incomplete})
}

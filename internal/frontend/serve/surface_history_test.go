package serve

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/state/store"
)

type surfaceCtl struct {
	extensionCtl
	path     string
	surfaces []event.ExtensionSurfacePayload
}

func (c *surfaceCtl) SessionPath() string                                { return c.path }
func (c *surfaceCtl) ExtensionSurfaces() []event.ExtensionSurfacePayload { return c.surfaces }

func TestSurfaceMarkerNeverRecordsPluginContent(t *testing.T) {
	path := filepath.Join(testenv.TempDir(t), "session.jsonl")
	var log wireLog
	log.write(path, []byte(`{"kind":"extension_surface","extension":{"pluginId":"alpha","surfaceId":"form","kind":"form","form":{"message":"PRIVATE","fields":[{"key":"secret","kind":"input","default":"SECRET"}]}}}`))
	log.write(path, []byte(`{"kind":"extension_surface","extension":{"pluginId":"alpha","surfaceId":"toast","kind":"notification","notification":{"title":"DO NOT REPLAY"}}}`))
	data, err := os.ReadFile(store.SessionWireLog(path))
	if err != nil {
		t.Fatal(err)
	}
	for _, private := range []string{"PRIVATE", "SECRET", "default", "DO NOT REPLAY", "toast"} {
		if strings.Contains(string(data), private) {
			t.Fatalf("recorded content: %s", data)
		}
	}
	if !strings.Contains(string(data), event.SurfaceExpired) {
		t.Fatalf("no expiry identity: %s", data)
	}
}

func TestSurfaceRecoveryUsesLiveStateAndExpiresAfterRestart(t *testing.T) {
	path := filepath.Join(testenv.TempDir(t), "session.jsonl")
	var log wireLog
	log.write(path, []byte(`{"kind":"extension_surface","extension":{"pluginId":"alpha","surfaceId":"view","kind":"view","view":{"body":[{"kind":"text","value":"PRIVATE"}]}}}`))
	ctl := &surfaceCtl{path: path, surfaces: []event.ExtensionSurfacePayload{{PluginID: "alpha", SurfaceID: "view", Kind: "view", View: &event.ExtensionViewSurface{Body: []event.ExtensionViewNode{{Kind: "text", Value: "live"}}}}}}
	s := New(ctl, NewBroadcaster(), config.ServeConfig{})
	for _, live := range []bool{true, false} {
		if !live {
			ctl.surfaces = nil
		}
		req, _ := http.NewRequest(http.MethodGet, "/extensions/surfaces", nil)
		rec := httptest.NewRecorder()
		s.extensionSurfaces(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
		}
		raw := rec.Body.Bytes()
		got := decodeReplay(t, rec).Surfaces
		if len(got) != 1 {
			t.Fatalf("recovery: %s", raw)
		}
		if live && (got[0].View == nil || got[0].View.Body[0].Value != "live") {
			t.Fatalf("lost live view: %s", raw)
		}
		if !live && (got[0].Code != event.SurfaceExpired || got[0].View != nil) {
			t.Fatalf("old view stays live: %s", raw)
		}
	}
}

func decodeReplay(t *testing.T, rec *httptest.ResponseRecorder) surfaceReplay {
	t.Helper()
	var got surfaceReplay
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("body %s: %v", rec.Body.String(), err)
	}
	return got
}

func fetchSurfaces(s *Server) *httptest.ResponseRecorder {
	req, _ := http.NewRequest(http.MethodGet, "/extensions/surfaces", nil)
	rec := httptest.NewRecorder()
	s.extensionSurfaces(rec, req)
	return rec
}

const viewFrame = `{"kind":"extension_surface","extension":{"pluginId":"alpha","surfaceId":"view","kind":"view","view":{"body":[{"kind":"text","value":"x"}]}}}`

// fillTo grows a session's log past the cap with frames the log keeps (a turn
// per frame), so the next frame it is offered is refused and witnessed.
func fillTo(t *testing.T, log *wireLog, path string) {
	t.Helper()
	filler := []byte(`{"kind":"notice","text":"` + strings.Repeat("x", 4096) + `"}`)
	for range wireLogMaxBytes/len(filler) + 2 {
		log.write(path, filler)
	}
}

// A long session whose log hit its retention cap is the ordinary case, not a
// failure: with no extension surface ever offered, reopening it has nothing to
// restore and nothing to warn about.
func TestTruncatedLogWithNoSurfaceIsNotAnError(t *testing.T) {
	path := filepath.Join(testenv.TempDir(t), "session.jsonl")
	var log wireLog
	fillTo(t, &log, path)
	log.write(path, []byte(`{"kind":"notice","text":"refused"}`))
	if witnessed, err := truncationWitnessed(store.SessionWireLogMeta(path)); err != nil || !witnessed {
		t.Fatalf("setup: the log must be truncated (witnessed=%v err=%v)", witnessed, err)
	}
	rec := fetchSurfaces(New(&surfaceCtl{path: path}, NewBroadcaster(), config.ServeConfig{}))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	if got := decodeReplay(t, rec); len(got.Surfaces) != 0 || got.Incomplete != "" {
		t.Fatalf("a truncated log with no surface in it answered %+v, want nothing and no warning", got)
	}
}

// What was recorded before the cap is still returned, and when a surface frame
// was the one refused the answer says surfaces may be missing.
func TestTruncatedLogThatDroppedASurfaceSaysSo(t *testing.T) {
	path := filepath.Join(testenv.TempDir(t), "session.jsonl")
	var log wireLog
	log.write(path, []byte(viewFrame))
	fillTo(t, &log, path)
	log.write(path, []byte(`{"kind":"extension_surface","extension":{"pluginId":"beta","surfaceId":"late","kind":"view","view":{"body":[{"kind":"text","value":"y"}]}}}`))
	rec := fetchSurfaces(New(&surfaceCtl{path: path}, NewBroadcaster(), config.ServeConfig{}))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	got := decodeReplay(t, rec)
	if len(got.Surfaces) != 1 || got.Surfaces[0].PluginID != "alpha" || got.Surfaces[0].Code != event.SurfaceExpired {
		t.Fatalf("surfaces = %+v, want the one recorded before the cap, expired", got.Surfaces)
	}
	if got.Incomplete != replayDropped {
		t.Fatalf("incomplete = %q, want %q: a surface frame was refused", got.Incomplete, replayDropped)
	}
	// The mark survives a restart: a fresh writer for the same log must not
	// forget it when it records a later refusal.
	var restarted wireLog
	restarted.write(path, []byte(`{"kind":"notice","text":"refused again"}`))
	if got := decodeReplay(t, fetchSurfaces(New(&surfaceCtl{path: path}, NewBroadcaster(), config.ServeConfig{}))); got.Incomplete != replayDropped {
		t.Fatalf("after a restart incomplete = %q, want it kept", got.Incomplete)
	}
}

// A refusal recorded before the mark existed says only that the log was cut.
// With nothing known about what was cut, it must not warn every reopen.
func TestOldTruncationRecordDoesNotWarn(t *testing.T) {
	path := filepath.Join(testenv.TempDir(t), "session.jsonl")
	var log wireLog
	log.write(path, []byte(viewFrame))
	if err := os.WriteFile(store.SessionWireLogMeta(path), []byte(`{"truncated":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	rec := fetchSurfaces(New(&surfaceCtl{path: path}, NewBroadcaster(), config.ServeConfig{}))
	got := decodeReplay(t, rec)
	if rec.Code != http.StatusOK || len(got.Surfaces) != 1 || got.Incomplete != "" {
		t.Fatalf("old record = %d %+v, want the surface and no warning", rec.Code, got)
	}
}

// Only a log that cannot be read is an error, and it has a code of its own.
func TestUnreadableSurfaceHistoryIsATypedError(t *testing.T) {
	for name, damage := range map[string]func(path string){
		"a corrupt line":       func(p string) { _ = os.WriteFile(store.SessionWireLog(p), []byte("{not json}\n"), 0o600) },
		"an unreadable record": func(p string) { _ = os.WriteFile(store.SessionWireLogMeta(p), []byte("{not json"), 0o600) },
	} {
		path := filepath.Join(testenv.TempDir(t), "session.jsonl")
		damage(path)
		rec := fetchSurfaces(New(&surfaceCtl{path: path}, NewBroadcaster(), config.ServeConfig{}))
		var body struct {
			Code string `json:"code"`
		}
		_ = json.Unmarshal(rec.Body.Bytes(), &body)
		if rec.Code != http.StatusInternalServerError || body.Code != "surface.replay_unreadable" {
			t.Errorf("%s = %d %s, want 500 surface.replay_unreadable", name, rec.Code, rec.Body.String())
		}
	}
}

package serve

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reasonix/internal/contract/event"
	"reasonix/internal/session/control"
	"reasonix/internal/state/sessionstore"
	"reasonix/internal/state/store"
	"runtime"
	"strings"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
	"reasonix/internal/platform/remotecloud"
)

type treeReplay struct {
	Watermark int64             `json:"watermark"`
	Frames    []json.RawMessage `json:"frames"`
	Complete  bool              `json:"complete"`
}

func TestTreeChangesReachBothClientsAndReplayAfterDisconnect(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	root := testenv.TempDir(t)
	h := NewHub(HubOptions{})
	hubRuntime(t, h, root)
	path := filepath.Join(SessionDirFor(root), "shared.jsonl")
	writeSessionAt(t, path)
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	cursor := hubGet[treeReplay](t, srv, "/tree/events/replay?lastEventId=0").Watermark
	readPhone := func() []treeWorkspace {
		t.Helper()
		response, err := h.CloudDesktop(t.Context(), remotecloud.DesktopRequest{Method: http.MethodGet, Path: "/tree"}, "phone")
		if err != nil || response.Status != http.StatusOK {
			t.Fatalf("phone tree: %v, status %d", err, response.Status)
		}
		var tree []treeWorkspace
		if err := json.Unmarshal(response.Body, &tree); err != nil {
			t.Fatal(err)
		}
		return tree
	}
	for _, step := range []struct {
		route                   string
		body                    map[string]any
		archived, pinned, empty bool
	}{
		{"pin", map[string]any{"path": path, "pinned": true}, false, true, false},
		{"archive", map[string]any{"path": path, "archived": true}, true, false, false},
		{"archive", map[string]any{"path": path, "archived": false}, false, false, false},
		{"pin", map[string]any{"path": path, "pinned": false}, false, false, false},
		{"remove", map[string]any{"path": path}, false, false, true},
	} {
		data, _ := json.Marshal(step.body)
		response, err := http.Post(srv.URL+"/tree/sessions/"+step.route, "application/json", bytes.NewReader(data))
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusNoContent {
			t.Fatalf("%s: %d", step.route, response.StatusCode)
		}
		replay := hubGet[treeReplay](t, srv, "/tree/events/replay?lastEventId=0")
		if replay.Watermark <= cursor || len(replay.Frames) == 0 || !replay.Complete {
			t.Fatalf("%s did not invalidate both clients: %+v", step.route, replay)
		}
		cursor = replay.Watermark
		desktop := hubGet[[]treeWorkspace](t, srv, "/tree")
		phone := readPhone()
		left, _ := json.Marshal(desktop)
		right, _ := json.Marshal(phone)
		if !bytes.Equal(left, right) {
			t.Fatalf("%s: desktop and phone IDs/metadata differ", step.route)
		}
		rows := desktop[0].Sessions
		if step.empty {
			if len(rows) != 0 {
				t.Fatal("successful empty tree did not clear the deleted ID")
			}
			continue
		}
		if len(rows) != 1 || rows[0].Path != path || rows[0].Archived != step.archived || rows[0].Pinned != step.pinned {
			t.Fatalf("%s tree: %+v", step.route, rows)
		}
	}
	response, err := h.CloudDesktop(t.Context(), remotecloud.DesktopRequest{Method: http.MethodGet, Path: "/tree/events/replay?lastEventId=0"}, "phone")
	if err != nil || response.Status != http.StatusOK {
		t.Fatalf("phone reconnect replay: %v, %d", err, response.Status)
	}
	var replay treeReplay
	if err := json.Unmarshal(response.Body, &replay); err != nil {
		t.Fatal(err)
	}
	if replay.Watermark != cursor || len(replay.Frames) < 5 {
		t.Fatalf("reconnect missed mutations: %+v", replay)
	}
}

func TestTreeInvalidationSkipsFailedWritesAndUnrelatedEvents(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	h := NewHub(HubOptions{})
	rt := hubRuntime(t, h, testenv.TempDir(t))
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	before := hubGet[treeReplay](t, srv, "/tree/events/replay").Watermark
	response, err := http.Post(srv.URL+"/tree/sessions/pin", "application/json", bytes.NewBufferString(`{"path":"/outside/session.jsonl","pinned":true}`))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusForbidden {
		t.Fatalf("outside pin: %d", response.StatusCode)
	}
	rt.Server.bc.Emit(event.Event{Kind: event.Text, Text: "delta"})
	for _, code := range []string{event.NoticeCodeContextReport, event.NoticeCodeCompacted, event.NoticeCodeJobFinished, event.NoticeCodeExtensionSkipped} {
		rt.Server.bc.Emit(event.Event{Kind: event.Notice, Code: code})
	}
	if got := hubGet[treeReplay](t, srv, "/tree/events/replay").Watermark; got != before {
		t.Fatalf("failed write or streaming delta invalidated tree: %d -> %d", before, got)
	}
	rt.Server.bc.Emit(event.Event{Kind: event.TurnDone})
	if got := hubGet[treeReplay](t, srv, "/tree/events/replay").Watermark; got != before+1 {
		t.Fatalf("turn completion did not invalidate tree: %d -> %d", before, got)
	}
}

func TestTreeEventsDeliverToTwoLiveClientsAndAReconnect(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	h := NewHub(HubOptions{})
	hubRuntime(t, h, testenv.TempDir(t))
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	connect := func(cursor int64) (*http.Response, *bufio.Scanner) {
		t.Helper()
		request, _ := http.NewRequestWithContext(t.Context(), http.MethodGet, fmt.Sprintf("%s/tree/events?lastEventId=%d", srv.URL, cursor), nil)
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { response.Body.Close() })
		return response, bufio.NewScanner(response.Body)
	}
	read := func(scanner *bufio.Scanner) int64 {
		t.Helper()
		for scanner.Scan() {
			raw, ok := strings.CutPrefix(scanner.Text(), "data: ")
			if !ok {
				continue
			}
			var frame struct {
				Seq  int64  `json:"seq"`
				Code string `json:"code"`
			}
			if err := json.Unmarshal([]byte(raw), &frame); err != nil {
				t.Fatal(err)
			}
			if frame.Code == "session_tree_changed" || strings.Contains(raw, `"kind":"stream_watermark"`) {
				return frame.Seq
			}
		}
		t.Fatalf("stream ended: %v", scanner.Err())
		return 0
	}
	a, left := connect(0)
	defer a.Body.Close()
	b, right := connect(0)
	defer b.Body.Close()
	if read(left) != h.changes.Watermark() || read(right) != h.changes.Watermark() {
		t.Fatal("attach must invalidate the snapshot without claiming a numbered mutation")
	}
	cursor := h.changes.Watermark()
	h.treeChanged()
	if read(left) != cursor+1 || read(right) != cursor+1 {
		t.Fatal("live clients missed the same invalidation")
	}
	a.Body.Close()
	h.treeChanged()
	c, again := connect(cursor + 1)
	defer c.Body.Close()
	if read(again) != cursor+2 {
		t.Fatal("reconnect did not replay the missed mutation")
	}
}

func TestTreeInvalidationFollowsAutomaticArchiveOnlyWhenRowsChange(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	h := NewHub(HubOptions{})
	root := testenv.TempDir(t)
	rt := hubRuntime(t, h, root)
	path := filepath.Join(SessionDirFor(root), "old.jsonl")
	writeSessionAt(t, path)
	old := time.Now().Add(-90 * 24 * time.Hour)
	if err := sessionstore.UpdateBranchMeta(path, false, func(m *sessionstore.BranchMeta) error { m.UpdatedAt = old; return nil }); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{path, store.SessionEventLog(path)} {
		if _, err := os.Stat(name); err == nil {
			if err := os.Chtimes(name, old, old); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := rt.Server.ctl().SaveAutoArchiveSettings(control.AutoArchiveSettings{Enabled: true, Days: 3}); err != nil {
		t.Fatal(err)
	}
	pinsSynced.Store(true)
	t.Cleanup(func() { pinsSynced.Store(false) })
	before := h.changes.Watermark()
	if n := rt.Server.sweepInactiveSessions(); n != 1 {
		t.Fatalf("archived %d, want 1", n)
	}
	if got := h.changes.Watermark(); got != before+1 {
		t.Fatalf("archive did not notify: %d -> %d", before, got)
	}
	if n := rt.Server.sweepInactiveSessions(); n != 0 {
		t.Fatalf("unchanged pass archived %d", n)
	}
	if got := h.changes.Watermark(); got != before+1 {
		t.Fatal("unchanged sweep notified clients")
	}
}

func TestTreeWriteKeepsJSONContentTypeAndEscaping(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	// Windows refuses < and > in a file name; & is the other character the JSON
	// encoder escapes, so the same check still holds there.
	name, raw := `<svg onload=alert(1)>`, "<svg"
	if runtime.GOOS == "windows" {
		name, raw = `&svg onload=alert(1)`, "&svg"
	}
	root := filepath.Join(testenv.TempDir(t), name)
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	h := NewHub(HubOptions{})
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	data, _ := json.Marshal(map[string]string{"path": root})
	response, err := http.Post(srv.URL+"/tree/workspaces", "application/json", bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != "application/json" || bytes.Contains(body, []byte(raw)) {
		t.Fatalf("unsafe JSON response: %d %s %s", response.StatusCode, response.Header.Get("Content-Type"), body)
	}
	var workspace treeWorkspace
	if err := json.Unmarshal(body, &workspace); err != nil || workspace.Root != root {
		t.Fatalf("JSON lost input: %s, %v", body, err)
	}
	if h.changes.Watermark() == 0 {
		t.Fatal("implicit 200 write did not invalidate")
	}
}

func TestTreeInvalidationDoesNotForwardSSHProxyHostChanges(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	far := NewHub(HubOptions{})
	root := testenv.TempDir(t)
	rt := hubRuntime(t, far, root)
	path := filepath.Join(SessionDirFor(root), "shared.jsonl")
	writeSessionAt(t, path)
	server := httptest.NewServer(operatorHandler(far))
	defer server.Close()
	near := NewHub(HubOptions{})
	if _, err := near.OpenRemote(RemoteEndpoint{Host: "ssh-host", Workspace: root, Addr: strings.TrimPrefix(server.URL, "http://"), Base: runtimePrefix + rt.ID}, nil); err != nil {
		t.Fatal(err)
	}
	beforeNear, beforeFar := near.changes.Watermark(), far.changes.Watermark()
	data, _ := json.Marshal(map[string]any{"path": path, "pinned": true})
	response, err := http.Post(server.URL+"/tree/sessions/pin", "application/json", bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNoContent || far.changes.Watermark() <= beforeFar {
		t.Fatal("far host mutation failed to notify its own clients")
	}
	if near.changes.Watermark() != beforeNear {
		t.Fatal("SSH proxy unexpectedly forwarded the far hub stream")
	}
}

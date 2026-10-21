package serve

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/runtime/tutor"
	"deep-tutor/internal/session/control"
)

// spaceTestServer builds a server whose tutor surface answers per space:
// the resolver reads a header, which is what a host's authenticated
// principal would name.
func spaceTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	root := t.TempDir()
	mk := func(name string) *tutor.Service {
		store, err := tutor.OpenStore(filepath.Join(root, name))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			for range 20 {
				if err := os.RemoveAll(filepath.Join(root, name)); err == nil {
					return
				}
				time.Sleep(25 * time.Millisecond)
			}
		})
		return tutor.NewService(store, tutor.ServiceOptions{})
	}
	SetTutorSpaceService("alice", mk("alice"))
	SetTutorSpaceService("bob", mk("bob"))
	t.Cleanup(func() {
		SetTutorSpaceService("alice", nil)
		SetTutorSpaceService("bob", nil)
	})
	SetTutorSpaceResolver(func(r *http.Request) string {
		return strings.TrimSpace(r.Header.Get("X-Test-Space"))
	})
	t.Cleanup(func() { SetTutorSpaceResolver(nil) })

	ctrl := control.New(control.Options{})
	s := New(ctrl, NewBroadcaster(), config.ServeConfig{})
	srv := httptest.NewServer(s.routes())
	t.Cleanup(srv.Close)
	return srv
}

func spacePost(t *testing.T, srv *httptest.Server, space, path, body string) (*http.Response, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, srv.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if space != "" {
		req.Header.Set("X-Test-Space", space)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	raw := spaceReadAll(t, resp)
	return resp, raw
}

func spaceGet(t *testing.T, srv *httptest.Server, space, path string) (*http.Response, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodGet, srv.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	if space != "" {
		req.Header.Set("X-Test-Space", space)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	raw := spaceReadAll(t, resp)
	return resp, raw
}

func spaceReadAll(t *testing.T, resp *http.Response) string {
	t.Helper()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// Two spaces are two stores: what one caller writes, another cannot read.
func TestTutorSpacesAreIsolated(t *testing.T) {
	srv := spaceTestServer(t)

	resp, body := spacePost(t, srv, "alice", "/tutor/sessions", `{"title":"alice 的会话"}`)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("create in alice: %d %s", resp.StatusCode, body)
	}
	var created struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(body), &created); err != nil || created.ID == "" {
		t.Fatalf("no session id in %s (%v)", body, err)
	}

	// Alice sees her session.
	resp, body = spaceGet(t, srv, "alice", "/tutor/sessions")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, created.ID) {
		t.Fatalf("alice's own list: %d %s", resp.StatusCode, body)
	}

	// Bob does not.
	resp, body = spaceGet(t, srv, "bob", "/tutor/sessions")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("bob's list: %d %s", resp.StatusCode, body)
	}
	if strings.Contains(body, created.ID) {
		t.Fatalf("bob sees alice's session: %s", body)
	}

	// And Bob cannot address it by id either.
	resp, body = spaceGet(t, srv, "bob", "/tutor/sessions/"+created.ID)
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("bob fetched alice's session: %d %s", resp.StatusCode, body)
	}
}

// A request without a space belongs to the default space, which is where
// the store already is.
func TestTutorDefaultSpaceIsTheWholeStore(t *testing.T) {
	srv := spaceTestServer(t)
	resp, body := spacePost(t, srv, "", "/tutor/sessions", `{"title":"默认空间"}`)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("create in the default space: %d %s", resp.StatusCode, body)
	}
	var created struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(body), &created); err != nil || created.ID == "" {
		t.Fatalf("no session id in %s (%v)", body, err)
	}
	// The default space is served by the process's own store, which this
	// test has no handle on; the request must at least be answered by a
	// tutor surface rather than refused as unavailable.
	if resp.StatusCode == http.StatusServiceUnavailable {
		t.Fatalf("the default space has no service: %s", body)
	}
}

// The space key becomes a directory name: it must not climb out of the
// spaces directory, and two keys that reduce alike stay apart.
func TestTutorSpaceSegmentIsSafeAndDistinct(t *testing.T) {
	if got := tutorSpaceSegment("../../etc"); strings.Contains(got, "..") || strings.Contains(got, "/") {
		t.Fatalf("the segment keeps a path separator: %q", got)
	}
	if got := tutorSpaceSegment(`..\..\windows`); strings.Contains(got, "\\") {
		t.Fatalf("the segment keeps a backslash: %q", got)
	}
	a := tutorSpaceSegment("a/b")
	b := tutorSpaceSegment("a_b")
	if a == b {
		t.Fatalf("two different keys reduce to the same directory: %q", a)
	}
	if tutorSpaceSegment("a/b") != a {
		t.Fatal("the reduction is not deterministic")
	}
}

// The default space keeps the store where it is, so an existing
// installation needs no migration; a named space is a subtree of its own.
func TestTutorSpaceRootKeepsTheLegacyStore(t *testing.T) {
	root := filepath.Join("C:", "state", "tutor")
	if got := tutorSpaceRoot(root, tutorDefaultSpace); got != root {
		t.Fatalf("the default space moved the store: %q", got)
	}
	got := tutorSpaceRoot(root, "alice")
	want := filepath.Join(root, "spaces", tutorSpaceSegment("alice"))
	if got != want {
		t.Fatalf("space root = %q, want %q", got, want)
	}
	if !strings.HasPrefix(got, root) {
		t.Fatalf("the space root left the store: %q", got)
	}
}

// A server-level service is one service over one store: it must not answer
// for a named space, or the isolation would hold only in name while every
// caller read the default space's data.
func TestTutorServerServiceDoesNotLeakIntoNamedSpaces(t *testing.T) {
	root := t.TempDir()
	store, err := tutor.OpenStore(filepath.Join(root, "default"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		for range 20 {
			if err := os.RemoveAll(filepath.Join(root, "default")); err == nil {
				return
			}
			time.Sleep(25 * time.Millisecond)
		}
	})
	svc := tutor.NewService(store, tutor.ServiceOptions{})

	ctrl := control.New(control.Options{})
	s := New(ctrl, NewBroadcaster(), config.ServeConfig{})
	s.SetTutorService(svc)
	SetTutorSpaceResolver(func(r *http.Request) string {
		return strings.TrimSpace(r.Header.Get("X-Test-Space"))
	})
	t.Cleanup(func() { SetTutorSpaceResolver(nil) })

	srv := httptest.NewServer(s.routes())
	t.Cleanup(srv.Close)

	// The default space is answered by the server-level service.
	resp, body := spacePost(t, srv, "", "/tutor/sessions", `{"title":"默认空间"}`)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("create in the default space: %d %s", resp.StatusCode, body)
	}
	var created struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(body), &created); err != nil || created.ID == "" {
		t.Fatalf("no session id in %s (%v)", body, err)
	}

	// A named space must not be answered out of that store.
	resp, body = spaceGet(t, srv, "carol", "/tutor/sessions")
	if resp.StatusCode == http.StatusOK && strings.Contains(body, created.ID) {
		t.Fatalf("the named space was answered by the server-level service: %s", body)
	}
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("unexpected status for the named space: %d %s", resp.StatusCode, body)
	}
}

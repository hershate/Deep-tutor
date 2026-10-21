package serve

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A path segment that tries to climb out of the tutor namespace must be
// refused. Go's ServeMux unescapes every segment before binding it to a
// wildcard, so "%2e%2e%2f" reaches a handler as "../" — before the guard the
// request could name a file outside the store root.
func TestTutorPathGuardRejectsTraversal(t *testing.T) {
	srv, store := newShareTestServer(t)

	// A file outside the notes directory that the traversal would reach.
	outside := filepath.Join(store.Root(), "plans", "index.json")
	if err := os.MkdirAll(filepath.Dir(outside), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(outside, []byte(`{"plans":[]}`), 0o644); err != nil {
		t.Fatal(err)
	}

	for _, path := range []string{
		"/tutor/notes/%2e%2e%2f%2e%2e%2fplans%2findex",
		"/tutor/notes/..%2f..%2fplans%2findex",
		"/tutor/notes/%2e%2e%5c%2e%2e%5cplans%5cindex",
	} {
		req, err := http.NewRequest(http.MethodDelete, srv.URL+path, nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		raw := make([]byte, 4096)
		n, _ := resp.Body.Read(raw)
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("traversal %s: %d %s", path, resp.StatusCode, raw[:n])
		}
		if _, err := os.Stat(outside); err != nil {
			t.Fatalf("traversal %s reached outside the store: %v", path, err)
		}
	}
}

// A normal id still works: the guard must not break the ordinary surface.
func TestTutorPathGuardAllowsNormalIDs(t *testing.T) {
	srv, store := newShareTestServer(t)

	sess, err := store.CreateSession("正常会话")
	if err != nil {
		t.Fatal(err)
	}
	resp, body := tutorGet(t, srv, "/tutor/sessions/"+sess.ID)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("normal id: %d %s", resp.StatusCode, body)
	}
	if !strings.Contains(body, sess.ID) {
		t.Fatalf("session body: %s", body)
	}
}

// The body a route may read is bounded: the store clamps what it stores, but
// the body is read before that clamp runs.
func TestTutorBodyCapRejectsOversizedBody(t *testing.T) {
	srv, store := newShareTestServer(t)

	oversized := map[string]string{"title": strings.Repeat("x", int(tutorDefaultBodyCap)+1024)}
	resp, body := tutorPost(t, srv, "/tutor/sessions", oversized)
	if resp.StatusCode == http.StatusOK {
		t.Fatalf("oversized body accepted: %d %s", resp.StatusCode, body)
	}
	sessions, err := store.Sessions()
	if err != nil {
		t.Fatal(err)
	}
	if len(sessions) != 0 {
		t.Fatalf("oversized body created %d sessions", len(sessions))
	}
}

// A small body still passes: the cap must not break the ordinary surface.
func TestTutorBodyCapAllowsNormalBody(t *testing.T) {
	srv, _ := newShareTestServer(t)

	resp, body := tutorPost(t, srv, "/tutor/sessions", map[string]string{"title": "正常会话"})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("normal body: %d %s", resp.StatusCode, body)
	}
}

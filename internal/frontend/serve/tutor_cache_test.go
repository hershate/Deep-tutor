package serve

import (
	"net/http"
	"testing"
)

// Everything under /tutor/ is one person's private learning data. An
// intermediary that stores a note list, a notes ZIP or a knowledge-base
// export and later hands it to the next reader has leaked it, so the whole
// surface says no-store.
func TestTutorSurfaceIsNotCacheable(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)
	for _, path := range []string{
		"/tutor/sessions",
		"/tutor/notes",
		"/tutor/plans",
		"/tutor/usage",
		"/tutor/kb/documents",
		"/tutor/shares",
	} {
		resp, body := tutorGet(t, srv, path)
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("%s: %d %s", path, resp.StatusCode, body)
		}
		if got := resp.Header.Get("Cache-Control"); got != "no-store" {
			t.Fatalf("%s: Cache-Control = %q, want no-store", path, got)
		}
	}
}

// The exports carry the user's own content as a file — the notes ZIP is the
// sharpest case: it is the whole note set in one archive.
func TestTutorExportIsNotCacheable(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)
	resp, body := tutorPost(t, srv, "/tutor/notes", map[string]string{"title": "私密笔记", "body": "只有我能看"})
	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusOK {
		t.Fatalf("create note: %d %s", resp.StatusCode, body)
	}
	resp, body = tutorGet(t, srv, "/tutor/notes/export.zip")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("notes zip: %d %s", resp.StatusCode, body)
	}
	if got := resp.Header.Get("Cache-Control"); got != "no-store" {
		t.Fatalf("Cache-Control = %q, want no-store", got)
	}
}

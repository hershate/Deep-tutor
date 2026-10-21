package serve

import (
	"encoding/base64"
	"net/http"
	"strings"
	"testing"
)

// A second backup operation must be refused while one is in flight: the
// import path holds a whole-store document in memory, so concurrent imports
// would multiply the peak (Deep Tutor takes _RESTORE_LOCK for the same
// reason).
func TestTutorBackupSecondOperationConflicts(t *testing.T) {
	srv, _ := newShareTestServer(t)

	tutorBackupMu.Lock()
	resp, body := tutorPost(t, srv, "/tutor/backup/preview", map[string]string{"content_base64": "e30="})
	if resp.StatusCode != http.StatusConflict {
		tutorBackupMu.Unlock()
		t.Fatalf("busy preview: %d %s", resp.StatusCode, body)
	}
	if !strings.Contains(body, "tutor.backup_busy") {
		tutorBackupMu.Unlock()
		t.Fatalf("busy preview body: %s", body)
	}

	resp, body = tutorPost(t, srv, "/tutor/backup/import", map[string]string{"content_base64": "e30="})
	tutorBackupMu.Unlock()
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("busy import: %d %s", resp.StatusCode, body)
	}
	if !strings.Contains(body, "tutor.backup_busy") {
		t.Fatalf("busy import body: %s", body)
	}
}

// An oversized upload must be refused before the decoded copy is allocated.
func TestTutorBackupOversizedPayloadRefused(t *testing.T) {
	srv, _ := newShareTestServer(t)

	old := tutorBackupDecodedCap
	tutorBackupDecodedCap = 16
	t.Cleanup(func() { tutorBackupDecodedCap = old })

	payload := base64.StdEncoding.EncodeToString([]byte(strings.Repeat("x", 64)))
	resp, body := tutorPost(t, srv, "/tutor/backup/import", map[string]string{"content_base64": payload})
	if resp.StatusCode != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized import: %d %s", resp.StatusCode, body)
	}
	if !strings.Contains(body, "tutor.backup_too_large") {
		t.Fatalf("oversized import body: %s", body)
	}
}

// The lock must be released again: a refused request may not wedge the
// surface.
func TestTutorBackupLockReleasedAfterRequest(t *testing.T) {
	srv, _ := newShareTestServer(t)

	doc := `{"format":"reasonix-tutor-backup","version":1,"sessions":[],"notes":[],"kb":[]}`
	b64 := base64.StdEncoding.EncodeToString([]byte(doc))
	resp, body := tutorPost(t, srv, "/tutor/backup/preview", map[string]string{"content_base64": b64})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("preview after release: %d %s", resp.StatusCode, body)
	}
	if !tutorBackupMu.TryLock() {
		t.Fatal("backup lock still held after the request finished")
	}
	tutorBackupMu.Unlock()
}

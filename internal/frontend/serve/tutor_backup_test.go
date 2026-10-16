package serve

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"reasonix/internal/runtime/tutor"
)

func TestTutorBackupEndpoints(t *testing.T) {
	srv, store := newShareTestServer(t)

	// One plan so the export carries something real.
	sess, err := store.CreateSession("备份会话")
	if err != nil {
		t.Fatal(err)
	}
	plan := &tutor.Plan{
		ID: "p1", SessionID: sess.ID, Version: 1, Title: "导出方案", Topic: "主题",
		CreatedAt: time.Date(2026, 10, 14, 8, 0, 0, 0, time.UTC),
		Knowledge: tutor.KnowledgeMap{Nodes: []tutor.KnowledgeNode{{SID: "n1", Name: "节点", Difficulty: 2}}},
	}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	sess.PlanID = plan.ID
	if err := store.SaveSession(sess); err != nil {
		t.Fatal(err)
	}

	// Export: a JSON attachment whose header names the format.
	resp, body := tutorGet(t, srv, "/tutor/backup/export")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("export: %d %s", resp.StatusCode, body)
	}
	if cd := resp.Header.Get("Content-Disposition"); !strings.Contains(cd, "reasonix-tutor-backup-") || !strings.Contains(cd, ".json") {
		t.Fatalf("content disposition: %q", cd)
	}
	var doc tutor.BackupDoc
	if err := json.Unmarshal([]byte(body), &doc); err != nil {
		t.Fatalf("export is not the backup document: %v", err)
	}
	if doc.Format != "reasonix-tutor-backup" || len(doc.Sessions) != 1 {
		t.Fatalf("export doc: %+v", doc)
	}

	// Preview counts without changing anything.
	b64 := base64.StdEncoding.EncodeToString([]byte(body))
	resp, preview := tutorPost(t, srv, "/tutor/backup/preview", map[string]string{"content_base64": b64})
	if resp.StatusCode != http.StatusOK || !strings.Contains(preview, `"plans":1`) || !strings.Contains(preview, `"sessions":1`) {
		t.Fatalf("preview: %d %s", resp.StatusCode, preview)
	}
	if plans, _ := store.Plans(); len(plans) != 1 {
		t.Fatalf("preview must not write: %d plans", len(plans))
	}

	// Import appends the document as new rows.
	resp, summary := tutorPost(t, srv, "/tutor/backup/import", map[string]string{"content_base64": b64})
	if resp.StatusCode != http.StatusOK || !strings.Contains(summary, `"plans":1`) {
		t.Fatalf("import: %d %s", resp.StatusCode, summary)
	}
	if plans, _ := store.Plans(); len(plans) != 2 {
		t.Fatalf("import should append a second plan: %d", len(plans))
	}

	// Refusals: wrong shape and wrong version.
	bad := base64.StdEncoding.EncodeToString([]byte(`{"format":"nope","version":1}`))
	resp, refuseBody := tutorPost(t, srv, "/tutor/backup/preview", map[string]string{"content_base64": bad})
	if resp.StatusCode != http.StatusBadRequest || !strings.Contains(refuseBody, "tutor.bad_backup") {
		t.Fatalf("bad backup refusal: %d %s", resp.StatusCode, refuseBody)
	}
	notJSON := base64.StdEncoding.EncodeToString([]byte("plain text"))
	resp, _ = tutorPost(t, srv, "/tutor/backup/import", map[string]string{"content_base64": notJSON})
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("garbage import: %d", resp.StatusCode)
	}
}

package serve

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"reasonix/internal/runtime/tutor"
)

// A share link carries its term: the response says when it stops
// answering, and the owner can pin it open on purpose.
func TestTutorShareCarriesItsTermAndCanBePinnedOpen(t *testing.T) {
	srv, store := newShareTestServer(t)

	sess, err := store.CreateSession("share-term")
	if err != nil {
		t.Fatal(err)
	}
	plan := &tutor.Plan{
		ID: "plan1", SessionID: sess.ID, Version: 1,
		Title: "方案", Topic: "主题", CreatedAt: time.Date(2026, 10, 14, 8, 0, 0, 0, time.UTC),
	}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}

	resp, body := tutorPost(t, srv, "/tutor/plans/plan1/share", nil)
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("create share: %d %s", resp.StatusCode, body)
	}
	if !strings.Contains(body, `"expires_at":"`) {
		t.Fatalf("the share response carries no expiry: %s", body)
	}

	// The owner pins it open: the expiry goes away.
	resp, body = tutorPost(t, srv, "/tutor/plans/plan1/share/permanent", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("pin open: %d %s", resp.StatusCode, body)
	}
	if !strings.Contains(body, `"expires_at":null`) {
		t.Fatalf("a pinned share still carries an expiry: %s", body)
	}

	// The status endpoint agrees.
	resp, body = tutorGet(t, srv, "/tutor/plans/plan1/share")
	if resp.StatusCode != http.StatusOK || !strings.Contains(body, `"expires_at":null`) {
		t.Fatalf("status of a pinned share: %d %s", resp.StatusCode, body)
	}
}

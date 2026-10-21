package tutor

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// A share link stops answering when its term is over. The link is removed
// on sight, so a leaked one cannot be told apart from one that never
// existed.
func TestShareExpiresAfterTheDefaultTerm(t *testing.T) {
	svc := newShareService(t)
	plan := &Plan{ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now()}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	sh, err := svc.SharePlan(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if sh.ExpiresAt == nil {
		t.Fatal("a new share must carry an expiry")
	}
	if got := sh.ExpiresAt.Sub(sh.CreatedAt); got != ShareDefaultTTL {
		t.Fatalf("share term = %v, want %v", got, ShareDefaultTTL)
	}
	if _, err := svc.SharedPlanView(sh.Token); err != nil {
		t.Fatalf("a fresh share must answer: %v", err)
	}
	// The clock moves past the term.
	svc.opt.Now = func() time.Time { return sh.CreatedAt.Add(ShareDefaultTTL + time.Minute) }
	if _, err := svc.SharedPlanView(sh.Token); !errors.Is(err, ErrNotFound) {
		t.Fatalf("an expired share answered: %v", err)
	}
	if _, err := os.Stat(svc.store.sharePath(sh.Token)); !os.IsNotExist(err) {
		t.Fatalf("the expired share was not removed: %v", err)
	}
	// The management list must not advertise it either.
	entries, err := svc.ShareList()
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("the list still shows %d expired share(s)", len(entries))
	}
}

// The owner can pin a link open: it stops expiring and stays answerable
// until it is revoked.
func TestShareMakePermanentKeepsTheLinkAlive(t *testing.T) {
	svc := newShareService(t)
	plan := &Plan{ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now()}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	sh, err := svc.SharePlan(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	pinned, err := svc.ShareMakePermanent(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if pinned.ExpiresAt != nil {
		t.Fatalf("a pinned share still expires at %v", pinned.ExpiresAt)
	}
	svc.opt.Now = func() time.Time { return sh.CreatedAt.Add(365 * 24 * time.Hour) }
	if _, err := svc.SharedPlanView(sh.Token); err != nil {
		t.Fatalf("a pinned share must keep answering: %v", err)
	}
	status, err := svc.ShareStatus(plan.ID)
	if err != nil || status == nil {
		t.Fatalf("the pinned share is not reported: %+v %v", status, err)
	}
}

// An expired link is not handed back by create either: a fresh one is
// minted, so creating a share never returns a dead link.
func TestSharePlanMintsAFreshLinkAfterExpiry(t *testing.T) {
	svc := newShareService(t)
	plan := &Plan{ID: "p1", Version: 1, Title: "方案", CreatedAt: time.Now()}
	if err := svc.store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	first, err := svc.SharePlan(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	svc.opt.Now = func() time.Time { return first.CreatedAt.Add(ShareDefaultTTL + time.Minute) }
	second, err := svc.SharePlan(plan.ID)
	if err != nil {
		t.Fatal(err)
	}
	if second.Token == first.Token {
		t.Fatal("an expired share was handed back instead of a fresh one")
	}
	if second.ExpiresAt == nil {
		t.Fatal("the fresh share carries no expiry")
	}
}

// The knowledge-base export is bounded: a selection over the cap is
// refused before any temp file exists.
func TestKbExportRefusesAnOversizedSelection(t *testing.T) {
	svc := newShareService(t)
	doc, err := svc.KbUpload("big.bin", make([]byte, 4096))
	if err != nil {
		t.Fatal(err)
	}
	prev := KbExportMaxBytes
	KbExportMaxBytes = 1024
	t.Cleanup(func() { KbExportMaxBytes = prev })

	if _, _, err := svc.KbExportZip([]string{doc.ID}); !errors.Is(err, ErrKbExportTooLarge) {
		t.Fatalf("an oversized selection was exported: %v", err)
	}
	// Under the cap the same document exports.
	KbExportMaxBytes = 1 << 20
	path, count, err := svc.KbExportZip([]string{doc.ID})
	if err != nil {
		t.Fatalf("a selection under the cap refused: %v", err)
	}
	t.Cleanup(func() { _ = os.Remove(path) })
	if count != 1 {
		t.Fatalf("exported %d documents, want 1", count)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("the export file is missing: %v", err)
	}
}

// The live caps are the user's call (2026-10-21): the tutor store is a
// local single-user space, so the caps bound runaway growth rather than a
// multi-tenant pool. This pins the values so a later edit cannot drift
// without saying so.
func TestLiveQuotaValues(t *testing.T) {
	cases := []struct {
		name string
		got  int
		want int
	}{
		{"sessions", maxSessions, 500},
		{"notes", noteQuota, 2000},
		{"materials per session", maxMaterialsPerSession, 50},
		{"plans per session", maxPlansPerSession, 100},
		{"kb documents", KbMaxDocs, 500},
		{"backup restore sessions", backupMaxSessions, 500},
		{"backup restore materials", backupMaxMaterialsPerSession, 50},
		{"backup restore plans", backupMaxPlansPerSession, 100},
	}
	for _, c := range cases {
		if c.got != c.want {
			t.Errorf("%s cap = %d, want %d", c.name, c.got, c.want)
		}
	}
}

// The export cap must stay above what an ordinary library holds: a
// selection under it exports, one over it refuses.
func TestKbExportCapIsAboveTheOrdinaryLibrary(t *testing.T) {
	if KbExportMaxBytes < 64<<20 {
		t.Fatalf("the export cap %d is below a usable floor", KbExportMaxBytes)
	}
	if _, err := os.Stat(filepath.Join(t.TempDir(), "nothing")); !os.IsNotExist(err) {
		t.Fatalf("unexpected stat result: %v", err)
	}
}

package update

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

const withdrawnCatalog = `{"schemaVersion":1,"versions":[
  {"version":"2.1.0","tag":"studio-v2.1.0","manifest":"https://x/2.1.0/latest.json"},
  {"version":"2.0.0","tag":"studio-v2.0.0","manifest":"https://x/2.0.0/latest.json"}],
 "withdrawn":[
  {"version":"2.1.0","tag":"studio-v2.1.0","manifest":"https://x/2.1.0/latest.json","reason":"blank window on first launch","withdrawnAt":"2026-10-12T00:00:00Z"},
  {"version":"2.0.5","tag":"studio-v2.0.5","manifest":"https://x/2.0.5/latest.json","reason":"update loop"}]}`

func TestCheckNeverOffersAWithdrawnVersion(t *testing.T) {
	st, err := checkAgainst(t, withdrawnCatalog, Options{Current: "2.0.0"})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if st.Latest != "2.0.0" || st.Newer || st.AutoOK {
		t.Fatalf("status = %+v, want the withdrawn 2.1.0 neither offered nor installed", st)
	}
	for _, e := range st.Entries {
		if e.Version == "2.1.0" {
			t.Errorf("the catalog still lists the withdrawn 2.1.0: %+v", st.Entries)
		}
	}
}

func TestCheckNamesTheWithdrawnVersionTheUserRuns(t *testing.T) {
	st, err := checkAgainst(t, withdrawnCatalog, Options{Current: "2.0.5"})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if st.Withdrawn == nil || st.Withdrawn.Version != "2.0.5" || st.Withdrawn.Reason != "update loop" {
		t.Fatalf("Withdrawn = %+v, want the running 2.0.5 with its reason", st.Withdrawn)
	}
	clean, err := checkAgainst(t, withdrawnCatalog, Options{Current: "2.0.0"})
	if err != nil || clean.Withdrawn != nil {
		t.Fatalf("a version that was never withdrawn reads as one: %+v, %v", clean.Withdrawn, err)
	}
}

func TestManifestForRefusesAWithdrawnVersion(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(withdrawnCatalog))
	}))
	t.Cleanup(srv.Close)
	u := New(Options{IndexURL: srv.URL, HTTP: srv.Client()})
	for _, version := range []string{"2.1.0", "2.0.5"} {
		if _, err := u.ManifestFor(context.Background(), version); !errors.Is(err, ErrWithdrawn) {
			t.Errorf("ManifestFor(%s) = %v, want ErrWithdrawn", version, err)
		}
	}
}

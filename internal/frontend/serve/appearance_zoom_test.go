package serve

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
)

func zoomHome(t *testing.T, stored string) {
	t.Helper()
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", filepath.Join(home, "home"))
	t.Setenv("REASONIX_STATE_HOME", filepath.Join(home, "state"))
	if stored == "" {
		return
	}
	if err := os.MkdirAll(filepath.Dir(config.UserConfigPath()), 0o755); err != nil {
		t.Fatal(err)
	}
	body := "[desktop.appearance]\nzoom = " + stored + "\n"
	if err := os.WriteFile(config.UserConfigPath(), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func zoomSave(t *testing.T, body map[string]any) appearanceView {
	t.Helper()
	raw, _ := json.Marshal(body)
	rec := httptest.NewRecorder()
	(&Server{}).saveAppearance(rec, httptest.NewRequest(http.MethodPost, "/appearance", bytes.NewReader(raw)))
	if rec.Code != http.StatusOK {
		t.Fatalf("save = %d %s", rec.Code, rec.Body)
	}
	var out appearanceView
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func zoomRead(t *testing.T) appearanceView {
	t.Helper()
	rec := httptest.NewRecorder()
	(&Server{}).appearance(rec, httptest.NewRequest(http.MethodGet, "/appearance", nil))
	var out appearanceView
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestAppearanceAnnouncesTheRangeItClamps(t *testing.T) {
	zoomHome(t, "")
	got := zoomRead(t)
	want := zoomRangeView{Min: config.ZoomMin, Max: config.ZoomMax, Step: config.ZoomStep}
	if got.ZoomRange != want {
		t.Fatalf("range = %+v, want the config bounds %+v", got.ZoomRange, want)
	}
}

func TestSavedZoomIsClampedToTheAnnouncedRange(t *testing.T) {
	zoomHome(t, "")
	for _, c := range []struct{ sent, want float64 }{
		{2.0, config.ZoomMax},
		{0.5, config.ZoomMin},
		{1.25, 1.25},
		{0, 0},
	} {
		if got := zoomSave(t, map[string]any{"zoom": c.sent}).Zoom; got != c.want {
			t.Errorf("zoom %v saved as %v, want %v", c.sent, got, c.want)
		}
	}
}

func TestOutOfRangeStoredZoomReadsClampedAndSurvivesUnrelatedSaves(t *testing.T) {
	for _, stored := range []string{"2.5", "0.7"} {
		zoomHome(t, stored)
		want := config.ClampZoom(map[string]float64{"2.5": 2.5, "0.7": 0.7}[stored])
		if got := zoomRead(t).Zoom; got != want {
			t.Fatalf("stored %s reads as %v, want %v", stored, got, want)
		}
		// The page posts the zoom it was shown; changing a font must not rewrite
		// the stored value the user never touched.
		zoomSave(t, map[string]any{"zoom": want, "fontUi": "Inter"})
		raw, err := os.ReadFile(config.UserConfigPath())
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(raw, []byte("zoom = "+stored)) {
			t.Fatalf("stored zoom %s was rewritten by an unrelated save:\n%s", stored, raw)
		}
		zoomSave(t, map[string]any{"zoom": 1.15})
		if got := zoomRead(t).Zoom; got != 1.15 {
			t.Fatalf("a deliberate change did not replace the stored value: %v", got)
		}
	}
}

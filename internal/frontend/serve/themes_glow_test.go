package serve

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"testing"

	"reasonix/internal/base/testenv"
)

func TestThemeGlowImportListAndReload(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	srv := themeServer(t)
	const raw = `{"schemaVersion":1,"id":"glow-probe","name":"Glow probe","tokens":{"light":{"bg":"#fff"},"dark":{"bg":"#111"}},"background":{"textGlow":"none"}}`
	response := postJSON(t, srv.URL+"/themes/import", map[string]any{
		"files": map[string]string{"theme.json": base64.StdEncoding.EncodeToString([]byte(raw))},
	})
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("import status=%d", response.StatusCode)
	}
	var imported struct {
		Pack struct {
			Background struct {
				TextGlow string `json:"textGlow"`
			} `json:"background"`
		} `json:"pack"`
	}
	if err := json.NewDecoder(response.Body).Decode(&imported); err != nil {
		t.Fatal(err)
	}
	if imported.Pack.Background.TextGlow != "none" {
		t.Fatalf("import lost glow: %+v", imported)
	}
	activation := postJSON(t, srv.URL+"/themes", map[string]string{"id": "glow-probe"})
	activation.Body.Close()
	if activation.StatusCode != http.StatusNoContent {
		t.Fatalf("activate status=%d", activation.StatusCode)
	}
	for _, base := range []string{srv.URL, themeServer(t).URL} {
		row := find(t, listThemes(t, base), "glow-probe")
		body, err := json.Marshal(row.Background)
		if err != nil {
			t.Fatal(err)
		}
		var bg map[string]any
		if err := json.Unmarshal(body, &bg); err != nil {
			t.Fatal(err)
		}
		if !row.Active || bg["textGlow"] != "none" {
			t.Fatalf("listing lost active glow after load: row=%+v background=%s", row, body)
		}
	}
}

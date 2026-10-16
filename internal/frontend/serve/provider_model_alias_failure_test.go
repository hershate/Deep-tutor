package serve

import (
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"reasonix/internal/contract/config"
)

func TestModelAliasFailedSaveDoesNotPublishAndCanRetry(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("requires enforced directory write permissions")
	}
	srv := newRichProviderServer(t)
	path := config.UserConfigPath()
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Dir(path)
	if err := os.Chmod(dir, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o700) })
	body := `{"name":"rich","models":["alpha","beta"],"default":"alpha","vision":[],"modelAliases":{"alpha":"Short"}}`
	resp := postProvider(t, srv.URL, "/providers/edit", body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("failed save = %d", resp.StatusCode)
	}
	after, err := os.ReadFile(path)
	if err != nil || string(before) != string(after) {
		t.Fatalf("failed save changed config: %v", err)
	}
	var got struct {
		Models []modelEntry `json:"models"`
	}
	getJSON(t, srv.URL+"/models", &got)
	for _, m := range got.Models {
		if m.ModelAlias != "" {
			t.Fatalf("failed alias was published: %+v", m)
		}
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	resp = postProvider(t, srv.URL, "/providers/edit", body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("retry = %d", resp.StatusCode)
	}
	p, _ := config.LoadForEdit(path).Provider("rich")
	if p.ModelAlias("alpha") != "Short" {
		t.Fatal("successful retry did not persist alias")
	}
}

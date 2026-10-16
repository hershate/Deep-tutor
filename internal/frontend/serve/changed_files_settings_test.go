package serve

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

func TestChangedFilesSettingIsShownByDefaultAndPersistsInTheUserFile(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()

	if got := readJSON[control.ChangedFilesSettings](t, srv.URL, "/changed-files-settings"); !got.Show {
		t.Fatalf("unset key reads %+v, want the pill shown", got)
	}
	off := postProvider(t, srv.URL, "/changed-files-settings", `{"show":false}`)
	off.Body.Close()
	if off.StatusCode != http.StatusOK {
		t.Fatalf("save = %d", off.StatusCode)
	}
	if config.LoadForEdit(config.UserConfigPath()).UI.ShowChangedFiles {
		t.Fatal("the choice did not reach [ui] show_changed_files")
	}
	if got := readJSON[control.ChangedFilesSettings](t, srv.URL, "/changed-files-settings"); got.Show {
		t.Fatalf("read back %+v, want hidden", got)
	}
	on := postProvider(t, srv.URL, "/changed-files-settings", `{"show":true}`)
	on.Body.Close()
	if !config.LoadForEdit(config.UserConfigPath()).UI.ShowChangedFiles {
		t.Fatal("turning it back on did not reach the file")
	}
}

func TestChangedFilesSettingRefusesABodyWithoutTheChoice(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()
	if got := readJSON[control.ChangedFilesSettings](t, srv.URL, "/changed-files-settings"); !got.Show {
		t.Fatalf("unset choice reads %+v, want the pill shown", got)
	}
	if got := readJSON[control.DisplayCurrencySettings](t, srv.URL, "/display-currency"); got.Mode != "auto" {
		t.Fatalf("currency reads %q, want auto", got.Mode)
	}
	resp := postProvider(t, srv.URL, "/changed-files-settings", `{}`)
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("empty body = %d, want 400: a missing choice must not read as off", resp.StatusCode)
	}
}

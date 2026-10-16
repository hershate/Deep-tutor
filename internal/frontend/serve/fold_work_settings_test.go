package serve

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

func TestFoldWorkSettingIsOffByDefaultAndPersistsInTheUserFile(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()

	if got := readJSON[control.FoldWorkSettings](t, srv.URL, "/fold-work-settings"); got.Fold {
		t.Fatalf("unset key reads %+v, want every step drawn", got)
	}
	on := postProvider(t, srv.URL, "/fold-work-settings", `{"fold":true}`)
	on.Body.Close()
	if on.StatusCode != http.StatusOK {
		t.Fatalf("save = %d", on.StatusCode)
	}
	if !config.LoadForEdit(config.UserConfigPath()).UI.FoldTurnWork {
		t.Fatal("the choice did not reach [ui] fold_turn_work")
	}
	if got := readJSON[control.FoldWorkSettings](t, srv.URL, "/fold-work-settings"); !got.Fold {
		t.Fatalf("read back %+v, want folding on", got)
	}
	off := postProvider(t, srv.URL, "/fold-work-settings", `{"fold":false}`)
	off.Body.Close()
	if config.LoadForEdit(config.UserConfigPath()).UI.FoldTurnWork {
		t.Fatal("turning it back off did not reach the file")
	}
}

func TestFoldWorkSettingRefusesABodyWithoutTheChoice(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()
	resp := postProvider(t, srv.URL, "/fold-work-settings", `{}`)
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("empty body = %d, want 400: a missing choice must not read as off", resp.StatusCode)
	}
}

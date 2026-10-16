package serve

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

func TestReceiptSettingIsHiddenByDefaultAndPersistsInTheUserFile(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()

	if got := readJSON[control.ReceiptSettings](t, srv.URL, "/receipt-settings"); got.Show {
		t.Fatalf("unset key reads %+v, want the card hidden", got)
	}
	resp := postProvider(t, srv.URL, "/receipt-settings", `{"show":true}`)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("save = %d", resp.StatusCode)
	}
	if !config.LoadForEdit(config.UserConfigPath()).UI.ShowReceipt {
		t.Fatal("the choice did not reach [ui] show_receipt")
	}
	if got := readJSON[control.ReceiptSettings](t, srv.URL, "/receipt-settings"); !got.Show {
		t.Fatalf("read back %+v, want shown", got)
	}
	off := postProvider(t, srv.URL, "/receipt-settings", `{"show":false}`)
	off.Body.Close()
	if config.LoadForEdit(config.UserConfigPath()).UI.ShowReceipt {
		t.Fatal("turning it off did not reach the file")
	}
}

func TestReceiptSettingRefusesABodyWithoutTheChoice(t *testing.T) {
	s := newProviderEditServer(t)
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()
	resp := postProvider(t, srv.URL, "/receipt-settings", `{}`)
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("empty body = %d, want 400: a missing choice must not read as off", resp.StatusCode)
	}
}

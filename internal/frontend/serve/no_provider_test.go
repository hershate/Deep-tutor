package serve

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

func noProviderServer(t *testing.T) *Server {
	t.Helper()
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", home)
	t.Setenv("REASONIX_CREDENTIALS_STORE", "file")
	if err := os.MkdirAll(filepath.Dir(config.UserConfigPath()), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config.UserConfigPath(), []byte("providers = []\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	bc := NewBroadcaster()
	ctrl := control.New(control.Options{Sink: bc, Label: "none", ModelRef: config.UnconfiguredRef, SessionDir: testenv.TempDir(t)})
	return New(ctrl, bc, config.ServeConfig{})
}

// With no provider the window has one job: ask for a connection. The setup
// state is how it is told, and it says so by a field, not by a missing key.
func TestNoProviderOwesSetupAndSaysWhy(t *testing.T) {
	s := noProviderServer(t)
	if !s.EnableProviderSetupForListener("127.0.0.1:8787") {
		t.Fatal("setup was not enabled")
	}
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()
	body := getProviderSetupBody(t, srv.URL+"/provider-setup")
	if !strings.Contains(body, `"required":true`) || !strings.Contains(body, `"noProvider":true`) {
		t.Fatalf("setup state = %s, want required and noProvider", body)
	}
}

func TestATurnWithNoProviderIsRefusedWithItsOwnCode(t *testing.T) {
	s := noProviderServer(t)
	s.EnableProviderSetupForListener("127.0.0.1:8787")
	rec := httptest.NewRecorder()
	if !s.refuseKeylessTurn(rec) {
		t.Fatal("a turn with no provider was not refused")
	}
	if rec.Code != http.StatusConflict || !strings.Contains(rec.Body.String(), `"provider.none_configured"`) {
		t.Fatalf("refusal = %d %s, want 409 provider.none_configured", rec.Code, rec.Body.String())
	}
}

// Adding the first provider is what ends the state, so the runtime must move to
// it: the model list and every later switch read from disk, but a controller
// started on the placeholder would otherwise keep answering "no provider".
func TestAddingTheFirstProviderMovesTheRuntimeToIt(t *testing.T) {
	s := noProviderServer(t)
	s.EnableProviderSetupForListener("127.0.0.1:8787")
	s.AllowProviderEdit()
	var rebuilt []string
	s.buildController = func(_ context.Context, ref string) (*control.Controller, error) {
		rebuilt = append(rebuilt, ref)
		return control.New(control.Options{Sink: s.bc, Label: "acme", ModelRef: ref, SessionDir: testenv.TempDir(t)}), nil
	}
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()

	body := `{"name":"acme","kind":"openai","baseUrl":"https://example.invalid/v1","apiKey":"sk-test","models":["m-1"],"default":"m-1"}`
	resp, err := http.Post(srv.URL+"/providers", "application/json", bytes.NewBufferString(body))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("saving the first provider answered %d", resp.StatusCode)
	}
	if len(rebuilt) != 1 || rebuilt[0] != "acme/m-1" {
		t.Fatalf("rebuilt = %v, want the runtime moved to acme/m-1", rebuilt)
	}
	if after := getProviderSetupBody(t, srv.URL+"/provider-setup"); strings.Contains(after, `"noProvider":true`) {
		t.Fatalf("setup still says no provider after one was added: %s", after)
	}
}

func TestInstallingAPresetFirstMovesTheRuntimeToo(t *testing.T) {
	s := noProviderServer(t)
	s.EnableProviderSetupForListener("127.0.0.1:8787")
	s.AllowProviderEdit()
	var rebuilt []string
	s.buildController = func(_ context.Context, ref string) (*control.Controller, error) {
		rebuilt = append(rebuilt, ref)
		return control.New(control.Options{Sink: s.bc, Label: "preset", ModelRef: ref, SessionDir: testenv.TempDir(t)}), nil
	}
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()
	presets := config.CuratedProviderPresets()
	if len(presets) == 0 {
		t.Skip("no curated preset to install")
	}
	body := `{"presetId":"` + presets[0].ID + `","apiKey":"sk-test"}`
	resp, err := http.Post(srv.URL+"/providers/presets/install", "application/json", bytes.NewBufferString(body))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || len(rebuilt) != 1 {
		t.Fatalf("install answered %d, rebuilt %v, want one rebuild onto the preset", resp.StatusCode, rebuilt)
	}
}

// Removing the last provider while the window is open: the window must be asked
// to connect now, not after the next launch, so the runtime moves to the
// stand-in as the provider goes.
func TestRemovingTheLastProviderSendsTheWindowToConnect(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", home)
	t.Setenv("REASONIX_CREDENTIALS_STORE", "file")
	if err := os.MkdirAll(filepath.Dir(config.UserConfigPath()), 0o755); err != nil {
		t.Fatal(err)
	}
	body := "default_model = \"only/m-1\"\n\n[[providers]]\nname = \"only\"\nkind = \"openai\"\nbase_url = \"https://example.invalid/v1\"\nmodels = [\"m-1\"]\ndefault = \"m-1\"\napi_key_env = \"ONLY_KEY\"\n"
	if err := os.WriteFile(config.UserConfigPath(), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	bc := NewBroadcaster()
	ctrl := control.New(control.Options{Sink: bc, Label: "m-1", ModelRef: "only/m-1", SessionDir: testenv.TempDir(t)})
	s := New(ctrl, bc, config.ServeConfig{})
	s.EnableProviderSetupForListener("127.0.0.1:8787")
	s.AllowProviderEdit()
	var rebuilt []string
	s.buildController = func(_ context.Context, ref string) (*control.Controller, error) {
		rebuilt = append(rebuilt, ref)
		return control.New(control.Options{Sink: s.bc, Label: "none", ModelRef: ref, SessionDir: testenv.TempDir(t)}), nil
	}
	srv := httptest.NewServer(operatorHandler(s))
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/providers/remove", "application/json", bytes.NewBufferString(`{"name":"only"}`))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if len(rebuilt) != 1 || rebuilt[0] != config.UnconfiguredRef {
		t.Fatalf("rebuilt = %v, want the runtime moved to %s", rebuilt, config.UnconfiguredRef)
	}
	if got := getProviderSetupBody(t, srv.URL+"/provider-setup"); !strings.Contains(got, `"noProvider":true`) {
		t.Fatalf("setup after removing the last provider = %s, want noProvider", got)
	}
	raw, _ := os.ReadFile(config.UserConfigPath())
	if !strings.Contains(string(raw), "providers = []") {
		t.Fatalf("the saved config does not say it has no provider:\n%s", raw)
	}
}

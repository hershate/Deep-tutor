package serve

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/session/control"
)

func TestModelAliasEditRoundTripKeepsModelIdentity(t *testing.T) {
	srv := newRichProviderServerAs(t, func(c control.SessionAPI) control.SessionAPI { return midTurn{c} })
	for _, aliases := range []string{`{"alpha":"  Short  ","beta":"Short"}`, `{"alpha":"  "}`} {
		resp := postProvider(t, srv.URL, "/providers/edit", `{"name":"rich","models":["alpha","beta"],"default":"alpha","vision":[],"modelAliases":`+aliases+`}`)
		resp.Body.Close()
		if resp.StatusCode != http.StatusNoContent {
			t.Fatalf("alias-only edit attempted a rebuild: status %d", resp.StatusCode)
		}
		var got struct {
			Current string           `json:"current"`
			Default string           `json:"default"`
			Models  []map[string]any `json:"models"`
		}
		getJSON(t, srv.URL+"/models", &got)
		if got.Current != "rich/alpha" || got.Default != "rich/alpha" {
			t.Fatalf("alias edit changed model identity: %+v", got)
		}
		want := "Short"
		if strings.Contains(aliases, `"  "`) {
			want = ""
		}
		for _, m := range got.Models {
			alias, _ := m["modelAlias"].(string)
			if alias != want || m["ref"] != "rich/"+m["model"].(string) {
				t.Fatalf("model projection = %+v, want alias %q with raw ref", m, want)
			}
		}
		var providers []struct {
			ModelAliases map[string]string `json:"modelAliases"`
		}
		getJSON(t, srv.URL+"/providers", &providers)
		if len(providers) != 1 || providers[0].ModelAliases["alpha"] != want {
			t.Fatalf("provider projection = %+v, want %q", providers, want)
		}
		cfg := config.LoadForEdit(config.UserConfigPath())
		p, ok := cfg.ResolveModel("rich/alpha")
		if !ok || p.Model != "alpha" || p.Default != "alpha" || cfg.DefaultModel != "rich/alpha" || p.PriceForModel("alpha") == nil {
			t.Fatalf("alias disturbed the resolved entry: %+v", p)
		}
		if _, ok := cfg.ResolveModel("rich/Short"); ok {
			t.Fatal("a display alias resolved as a wire model ID")
		}
	}
}

func TestModelAliasRefusalsLeaveConfigUnchanged(t *testing.T) {
	srv := newRichProviderServer(t)
	before, err := os.ReadFile(config.UserConfigPath())
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct{ aliases, code string }{
		{`{"alpha":"Valid","other":"No"}`, "provider.model_alias_unlisted"},
		{`{" alpha ":"No"}`, "provider.model_alias_unlisted"},
		{`{"alpha":"a\nb"}`, "provider.display_name_invalid"},
		{`{"alpha":"` + strings.Repeat("名", 65) + `"}`, "provider.display_name_too_long"},
	} {
		resp := postProvider(t, srv.URL, "/providers/edit", `{"name":"rich","models":["alpha","beta"],"default":"alpha","vision":[],"modelAliases":`+tc.aliases+`}`)
		code := reasonCode(t, resp)
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest || code != tc.code {
			t.Fatalf("%s: got %d %s, want 400 %s", tc.aliases, resp.StatusCode, code, tc.code)
		}
	}
	after, _ := os.ReadFile(config.UserConfigPath())
	if string(before) != string(after) {
		t.Fatal("a refused alias edit rewrote the config")
	}
}

func TestModelAliasesStayWithTheirProviderAndSurviveOlderClients(t *testing.T) {
	srv := newRichProviderServer(t)
	cfg := config.LoadForEdit(config.UserConfigPath())
	source, _ := cfg.Provider("rich")
	if err := source.SetModelAliases(map[string]string{"alpha": "First", "beta": "Retained"}); err != nil {
		t.Fatal(err)
	}
	other := *source
	other.Name, other.BaseURL, other.ModelAliases = "other", "https://other.invalid/v1", map[string]string{"alpha": "Second"}
	cfg.Providers = append(cfg.Providers, other)
	if err := cfg.SaveTo(config.UserConfigPath()); err != nil {
		t.Fatal(err)
	}
	resp := postProvider(t, srv.URL, "/providers/edit", `{"name":"rich","models":["alpha","beta"],"default":"alpha","vision":[]}`)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("older client edit = %d", resp.StatusCode)
	}
	var got struct {
		Models []modelEntry `json:"models"`
	}
	getJSON(t, srv.URL+"/models", &got)
	aliases := map[string]string{}
	for _, m := range got.Models {
		aliases[m.Ref] = m.ModelAlias
	}
	if aliases["rich/alpha"] != "First" || aliases["other/alpha"] != "Second" || aliases["rich/beta"] != "Retained" {
		t.Fatalf("aliases crossed identities: %+v", aliases)
	}
}

func TestModelAliasEditStillRequiresProviderGrant(t *testing.T) {
	s := newProviderEditServer(t)
	req := httptest.NewRequest(http.MethodPost, "/providers/edit", strings.NewReader(`{"name":"existing","models":["model-a"],"modelAliases":{"model-a":"Short"}}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	operatorHandler(s).ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden || !strings.Contains(rec.Body.String(), "provider.editing_disabled") {
		t.Fatalf("ungranted alias edit = %d %s", rec.Code, rec.Body.String())
	}
}

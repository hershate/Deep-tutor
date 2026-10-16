package serve

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/provider"
	"reasonix/internal/session/control"
)

func TestMachineDefaultUsesTheSelectedLocalCatalog(t *testing.T) {
	writeServeModelConfig(t)
	h := NewHub(HubOptions{})
	first := hubRuntime(t, h, testenv.TempDir(t))
	bc := NewBroadcaster()
	ctrl := control.New(control.Options{Sink: bc, WorkspaceRoot: testenv.TempDir(t),
		ProviderResolver: &provider.StaticResolver{Descriptors: []provider.Descriptor{{
			Ref: extensionModelRef, Model: "extension-chat", DisplayName: "Project extension",
		}}},
	})
	t.Cleanup(ctrl.Close)
	second, err := h.Adopt(New(ctrl, bc, config.ServeConfig{}), bc)
	if err != nil {
		t.Fatal(err)
	}
	for _, rt := range []*Runtime{first, second} {
		w := httptest.NewRecorder()
		operatorHandler(h).ServeHTTP(w, httptest.NewRequest(http.MethodGet, "http://localhost/host/settings/runtime/"+rt.ID+"/models?answers=all", nil))
		var body resolverModelsBody
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil || w.Code != http.StatusOK {
			t.Fatalf("models = %d %s: %v", w.Code, w.Body.String(), err)
		}
		offered := false
		for _, model := range body.Models {
			offered = offered || model.Ref == extensionModelRef
		}
		if offered != (rt == second) {
			t.Fatalf("runtime %s offers extension model = %v", rt.ID, offered)
		}
	}
	for _, tc := range []struct {
		name string
		base string
		want int
	}{
		{"unpinned", "/host/settings", http.StatusBadRequest},
		{"first", "/host/settings/runtime/" + first.ID, http.StatusBadRequest},
		{"second", "/host/settings/runtime/" + second.ID, http.StatusNoContent},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			r := httptest.NewRequest(http.MethodPost, "http://localhost"+tc.base+"/default-model", strings.NewReader(`{"ref":"`+extensionModelRef+`"}`))
			r.Header.Set("Content-Type", "application/json")
			operatorHandler(h).ServeHTTP(w, r)
			if w.Code != tc.want {
				t.Fatalf("%s/default-model = %d %s, want %d", tc.base, w.Code, w.Body.String(), tc.want)
			}
			if tc.want == http.StatusBadRequest && !strings.Contains(w.Body.String(), `"settings.unknown_model"`) {
				t.Fatalf("unknown model refusal = %s", w.Body.String())
			}
			if tc.want == http.StatusNoContent {
				if got := config.LoadForEdit(config.UserConfigPath()).DefaultModel; got != extensionModelRef {
					t.Fatalf("persisted default = %q, want %q", got, extensionModelRef)
				}
			}
		})
	}
}

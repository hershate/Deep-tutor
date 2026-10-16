package boot

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"reasonix/internal/base/netclient"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/provider"
)

func TestModelAliasDoesNotChangeBuildIdentityOrWireModel(t *testing.T) {
	gotModel := make(chan string, 2)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Model string `json:"model"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		gotModel <- body.Model
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n"))
	}))
	defer upstream.Close()
	cfg := &config.Config{Providers: []config.ProviderEntry{{Name: "relay", Kind: "openai", BaseURL: upstream.URL, Models: []string{"vendor/long-v1"}}}}
	entry, _ := cfg.ResolveModel("relay/vendor/long-v1")
	before := ResolveProviderBuildIdentity(entry, netclient.ProxySpec{}, nil)
	for _, aliases := range []map[string]string{nil, {"vendor/long-v1": "Short"}} {
		if err := cfg.Providers[0].SetModelAliases(aliases); err != nil {
			t.Fatal(err)
		}
		entry, _ = cfg.ResolveModel("relay/vendor/long-v1")
		if got := ResolveProviderBuildIdentity(entry, netclient.ProxySpec{}, nil); got != before {
			t.Fatal("display alias changed provider build identity")
		}
		p, err := NewProviderWithProxy(entry, netclient.ProxySpec{})
		if err != nil {
			t.Fatal(err)
		}
		stream, err := p.Stream(t.Context(), provider.Request{})
		if err != nil {
			t.Fatal(err)
		}
		for chunk := range stream {
			if chunk.Err != nil {
				t.Fatal(chunk.Err)
			}
		}
		if got := <-gotModel; got != "vendor/long-v1" {
			t.Fatalf("wire model = %q", got)
		}
	}
}

package plugin

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
)

func TestDiscoveryRefusesMetadataClaimingAnotherIssuer(t *testing.T) {
	var tokenHits atomic.Int64
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/token") {
			tokenHits.Add(1)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"issuer":                 "https://elsewhere.example.test",
			"authorization_endpoint": srv.URL + "/authorize",
			"token_endpoint":         srv.URL + "/token",
		})
	}))
	t.Cleanup(srv.Close)
	issuer, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := discoverAuthorizationServer(context.Background(), srv.Client(), issuer); err == nil || !strings.Contains(err.Error(), "issuer mismatch") {
		t.Fatalf("discovery error = %v, want an issuer mismatch", err)
	}
	if tokenHits.Load() != 0 {
		t.Fatal("a token endpoint advertised by mismatched metadata was contacted")
	}
}

func TestRefreshCredentialsGoOnlyToTheRecordedTokenEndpoint(t *testing.T) {
	var decoy atomic.Int64
	other := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { decoy.Add(1) }))
	t.Cleanup(other.Close)
	var got url.Values
	recorded := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		got = r.PostForm
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600})
	}))
	t.Cleanup(recorded.Close)
	dir := testenv.TempDir(t)
	state := mcpOAuthState{
		Version: 1, Resource: other.URL + "/mcp", Issuer: other.URL, TokenEndpoint: recorded.URL,
		ClientID: "c", ClientSecret: "s", TokenEndpointAuthMethod: "client_secret_post",
		AccessToken: "old", RefreshToken: "r", TokenType: "Bearer", Expiry: time.Now().Add(-time.Hour),
	}
	if err := saveMCPOAuthState(dir, state); err != nil {
		t.Fatal(err)
	}
	c := &mcpOAuthClient{stateDir: dir, state: state, client: http.DefaultClient}
	if _, _, err := c.authorizationHeader(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got.Get("refresh_token") != "r" || got.Get("client_secret") != "s" {
		t.Fatalf("the recorded endpoint did not receive the credentials: %v", got)
	}
	if decoy.Load() != 0 {
		t.Fatal("the MCP resource's own host received an OAuth credential request")
	}
}

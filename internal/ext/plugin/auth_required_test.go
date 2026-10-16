package plugin

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"reasonix/internal/base/testenv"
)

func answeringServer(t *testing.T, status int, challenge string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if challenge != "" {
			w.Header().Set("WWW-Authenticate", challenge)
		}
		http.Error(w, "this body mentions auth, oauth, unauthorized and forbidden", status)
	}))
	t.Cleanup(srv.Close)
	return srv
}

func probe(t *testing.T, srv *httptest.Server, headers map[string]string) error {
	t.Helper()
	tr, err := newHTTPTransport(Spec{Name: "remote", Type: "http", URL: srv.URL + "/mcp", Headers: headers, StateDir: testenv.TempDir(t)})
	if err != nil {
		t.Fatal(err)
	}
	defer tr.close()
	_, err = tr.call(context.Background(), "initialize", nil)
	if err == nil {
		t.Fatal("expected the server's refusal")
	}
	return err
}

func TestBearerChallengeCarriesAuthenticationIdentity(t *testing.T) {
	srv := answeringServer(t, http.StatusUnauthorized, `Bearer resource_metadata="https://x.test/.well-known/oauth-protected-resource", scope="mcp:connect"`)
	err := probe(t, srv, nil)
	if !errors.Is(err, ErrAuthenticationRequired) {
		t.Fatalf("401 with a Bearer challenge lost its identity: %v", err)
	}
	var need *AuthRequiredError
	if !errors.As(err, &need) || need.Server != "remote" || need.Scope != "mcp:connect" || need.Resource == "" {
		t.Fatalf("typed fields = %+v", need)
	}
	if terminalHTTPStatus(err) != http.StatusUnauthorized {
		t.Fatalf("status no longer readable through the identity: %d", terminalHTTPStatus(err))
	}
	got := InstallResultForError("remote", err)
	if got.State != "action_required" || got.Action != "authenticate" {
		t.Fatalf("install result = %+v", got)
	}
}

func TestOnlyTheTypedIdentityMeansSignIn(t *testing.T) {
	for _, tc := range []struct {
		name      string
		status    int
		challenge string
		headers   map[string]string
	}{
		{"401 without a challenge", http.StatusUnauthorized, "", nil},
		{"401 with a non-Bearer challenge", http.StatusUnauthorized, `Basic realm="x"`, nil},
		{"401 with explicit credentials", http.StatusUnauthorized, "Bearer", map[string]string{"Authorization": "Bearer wrong"}},
		{"403", http.StatusForbidden, `Bearer error="insufficient_scope"`, nil},
		{"500 whose body mentions auth", http.StatusInternalServerError, "", nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := probe(t, answeringServer(t, tc.status, tc.challenge), tc.headers)
			if errors.Is(err, ErrAuthenticationRequired) {
				t.Fatalf("promoted to the sign-in identity: %v", err)
			}
			if got := InstallResultForError("remote", err); got.State != "issue" || got.Action != "retry" {
				t.Fatalf("install result = %+v", got)
			}
		})
	}
	if got := InstallResultForError("remote", errors.New("oauth authorization forbidden")); got.State != "issue" {
		t.Fatalf("a sentence containing auth words decided the class: %+v", got)
	}
}

func TestRefreshStaysAtTheTokenEndpointStoredAtSignIn(t *testing.T) {
	var stored, advertised atomic.Int64
	storedAS := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		stored.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "access-new", "refresh_token": "refresh-new", "token_type": "Bearer", "expires_in": 3600})
	}))
	defer storedAS.Close()
	advertisedAS := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		advertised.Add(1)
		http.NotFound(w, r)
	}))
	defer advertisedAS.Close()
	mcp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/mcp" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"resource": "http://" + r.Host + "/mcp", "authorization_servers": []string{advertisedAS.URL},
			})
			return
		}
		if r.Header.Get("Authorization") != "Bearer access-new" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":1,"result":{}}`))
	}))
	defer mcp.Close()

	dir := testenv.TempDir(t)
	if err := saveMCPOAuthState(dir, mcpOAuthState{
		Version: 1, Resource: mcp.URL + "/mcp", Issuer: storedAS.URL,
		ClientID: "client", ClientSecret: "secret", TokenEndpoint: storedAS.URL + "/token", TokenEndpointAuthMethod: "client_secret_basic",
		AccessToken: "access-old", RefreshToken: "refresh-old", TokenType: "Bearer", Expiry: time.Now().Add(-time.Minute),
	}); err != nil {
		t.Fatal(err)
	}
	tr, err := newHTTPTransport(Spec{Name: "remote", Type: "http", URL: mcp.URL + "/mcp", StateDir: dir})
	if err != nil {
		t.Fatal(err)
	}
	defer tr.close()
	if _, err := tr.call(context.Background(), "ping", nil); err != nil {
		t.Fatal(err)
	}
	if stored.Load() != 1 || advertised.Load() != 0 {
		t.Fatalf("refresh requests: stored issuer %d, server-advertised issuer %d", stored.Load(), advertised.Load())
	}
}

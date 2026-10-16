package serve

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLoopbackEndpointIsReadFromTheAddress(t *testing.T) {
	for url, want := range map[string]bool{
		"http://localhost:11434/v1":  true,
		"http://127.0.0.1:11434":     true,
		"http://[::1]:8080/v1":       true,
		"http://api.example.com/v1":  false,
		"http://192.168.1.20:11434":  false,
		"https://localhost.evil.com": false,
		"":                           false,
	} {
		if got := loopbackEndpoint(url); got != want {
			t.Errorf("loopbackEndpoint(%q) = %v, want %v", url, got, want)
		}
	}
}

func TestOnlyATimeoutAgainstALocalAddressIsNamedLocal(t *testing.T) {
	if got := localTimeout("timeout", "http://localhost:11434"); got != reasonLocalTimeout {
		t.Errorf("local timeout = %q", got)
	}
	for _, c := range []struct{ reason, url string }{
		{"timeout", "https://api.example.com"}, {"network", "http://localhost:11434"}, {"auth", "http://127.0.0.1"},
	} {
		if got := localTimeout(c.reason, c.url); got != c.reason {
			t.Errorf("localTimeout(%q, %q) = %q, want unchanged", c.reason, c.url, got)
		}
	}
	if modelCheckTimeout("http://localhost:11434") <= modelCheckTimeout("https://api.example.com") {
		t.Error("a local address gets no longer wait than a remote one")
	}
}

// The user's case: a local engine that does not answer within the wait is a
// timeout the host can place at the machine's own address.
func TestModelCheckAgainstASlowLocalEngineSaysSo(t *testing.T) {
	old := modelCheckLocalTimeout
	modelCheckLocalTimeout = 200 * time.Millisecond
	t.Cleanup(func() { modelCheckLocalTimeout = old })

	release := make(chan struct{})
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer engine.Close()
	defer close(release)

	srv := newRichProviderServer(t)
	body := `{"model":"clef:27b","baseUrl":"` + engine.URL + `/v1","apiKey":"k","kind":"openai"}`
	resp := postProvider(t, srv.URL, "/providers/check/model", body)
	defer resp.Body.Close()
	var got struct {
		Status string `json:"status"`
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got.Status != "unknown" || got.Reason != "local_timeout" {
		t.Fatalf("check = %+v (%s), want unknown/local_timeout", got, strings.TrimSpace(resp.Status))
	}
}

package tutor

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"reasonix/internal/contract/config"
	"reasonix/internal/contract/provider"
	"reasonix/internal/tools/websearch"
)

func allowAll(string) error { return nil }

// fakeSearchModel is a provider whose searches answer a fixed sources text —
// the chunk shape Reasonix's websearch tool consumes.
type fakeSearchModel struct{ sources string }

func (f *fakeSearchModel) Name() string { return "fixture-search" }

func (f *fakeSearchModel) Stream(_ context.Context, _ provider.Request) (<-chan provider.Chunk, error) {
	ch := make(chan provider.Chunk, 2)
	ch <- provider.Chunk{Type: provider.ChunkProviderTool, Text: f.sources, ToolCall: &provider.ToolCall{Name: "web_search"}}
	ch <- provider.Chunk{Type: provider.ChunkDone}
	close(ch)
	return ch, nil
}

// newVerifyTargets serves the pages candidates land on.
func newVerifyTargets(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	long := strings.Repeat("React 学习笔记的内容，", 100)
	mux.HandleFunc("/ok/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "<html><body><p>"+long+"</p></body></html>")
	})
	mux.HandleFunc("/short", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "tiny")
	})
	mux.HandleFunc("/login", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "<html><body>"+long+"<div>please complete the captcha to continue</div></body></html>")
	})
	mux.HandleFunc("/hop", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/ok/react", http.StatusFound)
	})
	mux.HandleFunc("/gone", func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "not found", http.StatusNotFound)
	})
	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)
	return server
}

// fixtureSources is the sources text Reasonix's anthropic-style providers
// emit: a bold title line above each <url> bullet.
func fixtureSources(base string) string {
	return "\n- **React 官方教程**\n  <" + base + "/ok/react>\n" +
		"- **Hooks 指南**\n  <" + base + "/ok/hooks>\n" +
		"- <" + base + "/gone>\n"
}

func fixtureSearcher(t *testing.T, base string) *WebSearcher {
	t.Helper()
	return &WebSearcher{
		SearchModel: func() (provider.Provider, string, error) {
			return &fakeSearchModel{sources: fixtureSources(base)}, "fixture/search-model", nil
		},
		AllowHost: allowAll,
	}
}

func TestParseSourceLinks(t *testing.T) {
	sources := "\n- **React 官方教程**\n  <https://react.dev/learn>\n" +
		"- **Hooks 指南**\n  <https://react.dev/hooks>\n" +
		"- <https://legacy.example/x>\n" +
		"- <https://react.dev/learn>\n" +
		"- <ftp://skip.me/f>\n"
	got := parseSourceLinks(sources, 8)
	// dedup keeps one react.dev/learn; the ftp bullet never matches.
	if len(got) != 3 {
		t.Fatalf("candidates = %+v", got)
	}
	if got[0].Title != "React 官方教程" || got[0].URL != "https://react.dev/learn" {
		t.Fatalf("first = %+v", got[0])
	}
	if got[2].Title != "" || got[2].URL != "https://legacy.example/x" {
		t.Fatalf("bare bullet = %+v", got[2])
	}
	if limited := parseSourceLinks(sources, 2); len(limited) != 2 {
		t.Fatalf("cap ignored: %+v", limited)
	}
}

func TestEngineSearchRunsThroughWebSearchTool(t *testing.T) {
	w := &WebSearcher{}
	p, ref, err := w.searchModel()
	if err != nil || p != nil || ref != "" {
		t.Fatalf("unset selection = %v, %q, %v", p, ref, err)
	}
	w = &WebSearcher{SearchModel: func() (provider.Provider, string, error) {
		return &fakeSearchModel{sources: "\n- **React 官方教程**\n  <https://react.dev/learn>\n"}, "fixture/search-model", nil
	}}
	p, ref, err = w.searchModel()
	if err != nil || p == nil || ref != "fixture/search-model" {
		t.Fatalf("selection = %v, %q, %v", p, ref, err)
	}
	tool := &websearch.Tool{Factory: func() (provider.Provider, error) { return p, nil }}
	got, err := w.engineSearch(context.Background(), tool, "react 入门", 8)
	if err != nil {
		t.Fatalf("engineSearch: %v", err)
	}
	if len(got) != 1 || got[0].URL != "https://react.dev/learn" || got[0].Title != "React 官方教程" {
		t.Fatalf("candidates = %+v", got)
	}
	if empty, err := w.engineSearch(context.Background(), tool, "   ", 8); err != nil || empty != nil {
		t.Fatalf("blank query = %+v, %v", empty, err)
	}
}

func TestVerifyURLVerdicts(t *testing.T) {
	targets := newVerifyTargets(t)
	client := &http.Client{}
	cases := []struct {
		path string
		want string
	}{
		{"/ok/react", "ok"},
		{"/short", "uncertain"},
		{"/login", "uncertain"},
		{"/hop", "ok"}, // redirect followed to /ok
		{"/gone", "invalid"},
	}
	for _, tc := range cases {
		verdict := verifyURL(context.Background(), client, targets.URL+tc.path, allowAll)
		if verdict.Label != tc.want {
			t.Errorf("%s label = %q (reason %q), want %q", tc.path, verdict.Label, verdict.Reason, tc.want)
		}
	}
	ok := verifyURL(context.Background(), client, targets.URL+"/ok/react", allowAll)
	if !strings.Contains(ok.Snippet, "React") {
		t.Errorf("snippet = %q", ok.Snippet)
	}
	if _, cached := sharedVerifyCache.get(targets.URL + "/ok/react"); !cached {
		t.Error("verdict was not cached for the next caller")
	}
}

func TestSSRFGuardStillApplies(t *testing.T) {
	if err := requirePublicURL("http://169.254.169.254/latest/meta-data"); err == nil {
		t.Fatal("metadata endpoint allowed")
	}
	if err := requirePublicURL("http://127.0.0.1/x"); err == nil {
		t.Fatal("loopback allowed")
	}
	if err := requirePublicURL("ftp://example.com/f"); err == nil {
		t.Fatal("non-http scheme allowed")
	}
}

// The dial-time layer must refuse internal space even when the parse-time
// guard was bypassed — Reasonix's vetted-IP scheme under tutor policy.
func TestPublicOnlyDialRefusesInternalSpace(t *testing.T) {
	for _, addr := range []string{
		"127.0.0.1:80", "10.0.0.5:80", "192.168.1.4:80",
		"169.254.169.254:80", "100.100.100.200:80", "[fe80::1]:80",
	} {
		conn, err := publicOnlyDial((&net.Dialer{}).DialContext)(context.Background(), "tcp", addr)
		if err == nil {
			if conn != nil {
				conn.Close()
			}
			t.Fatalf("dial %s accepted", addr)
		}
	}
	if err := requirePublicIP(net.ParseIP("93.184.216.34")); err != nil {
		t.Fatalf("public ip refused: %v", err)
	}
}

func TestRobotsPermits(t *testing.T) {
	body := `User-agent: *
Disallow: /private/
Disallow:

User-agent: Reasonix-Tutor
Disallow: /tutor-only/`
	if !robotsPermits([]byte(body), "/learn/page") {
		t.Error("plain path should be permitted")
	}
	if robotsPermits([]byte(body), "/tutor-only/x") {
		t.Error("agent-specific rule ignored")
	}
	if robotsPermits([]byte(body), "/private/x") {
		t.Error("star-group disallow must still bind even when an agent group exists")
	}
}

func TestScreenParsingAndKeys(t *testing.T) {
	verdicts := parseScreenVerdicts("```json\n[{\"url\":\"https://a.dev/\",\"suitable\":\"yes\",\"label\":\"ok\"},{\"url\":\"https://b.dev/x#frag\",\"suitable\":0,\"label\":\"uncertain\",\"note\":\"过深\"}]\n```")
	if verdicts == nil {
		t.Fatal("verdicts nil")
	}
	if !verdicts["https://a.dev/"].Suitable || verdicts["https://a.dev/"].Label != VerifyOK {
		t.Fatalf("a verdict = %+v", verdicts["https://a.dev/"])
	}
	if verdicts["https://b.dev/x#frag"].Suitable || verdicts["https://b.dev/x#frag"].Note != "过深" {
		t.Fatalf("b verdict = %+v", verdicts["https://b.dev/x#frag"])
	}
	if parseScreenVerdicts("no json here") != nil {
		t.Fatal("garbage parsed")
	}
	if screenURLKey("HTTPS://A.DEV/x#f") != screenURLKey("https://a.dev/x/") {
		t.Fatalf("keys differ: %q vs %q", screenURLKey("HTTPS://A.DEV/x#f"), screenURLKey("https://a.dev/x/"))
	}
}

// newScreenLLM approves every URL it is asked about, optionally with edits.
func newScreenLLM(reply func(candidates string) string) *fakeLLM {
	return &fakeLLM{reply: func(system string, call int) string {
		_ = system
		return reply("candidates")
	}}
}

func TestWebSearcherEndToEndKeepsScreened(t *testing.T) {
	targets := newVerifyTargets(t)
	screened := newScreenLLM(func(string) string {
		return `[{"url":"` + targets.URL + `/ok/react","suitable":true,"label":"ok","note":""},` +
			`{"url":"` + targets.URL + `/ok/hooks","suitable":true,"label":"uncertain","note":"深度存疑"}]`
	})
	w := fixtureSearcher(t, targets.URL)
	w.Provider = func() (LLM, error) { return screened, nil }
	query := ResourceQuery{
		Topic: "React", Level: "零基础",
		Stages: []Stage{{Index: 0, Name: "入门", Difficulty: 1, Topics: []StageTopic{{Name: "react 入门"}}}},
		Nodes:  2,
	}
	outcome, err := w.Search(context.Background(), query)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if outcome.Engine != "fixture/search-model" {
		t.Fatalf("engine = %q", outcome.Engine)
	}
	// gone 死链在校验层剔除，去重后 2 条全过门。
	if len(outcome.Resources) != 2 {
		t.Fatalf("resources = %+v", outcome.Resources)
	}
	for _, r := range outcome.Resources {
		if r.Snippet != "" {
			t.Fatalf("snippet leaked into result: %+v", r)
		}
		if r.Verified == VerifyUncertain && r.Note != "深度存疑" {
			t.Fatalf("gate note lost: %+v", r)
		}
	}
}

func TestWebSearcherDropsAllWhenScreenFails(t *testing.T) {
	targets := newVerifyTargets(t)
	broken := &fakeLLM{reply: func(string, int) string { return "not json" }}
	w := fixtureSearcher(t, targets.URL)
	w.Provider = func() (LLM, error) { return broken, nil }
	outcome, err := w.Search(context.Background(), ResourceQuery{
		Topic:  "React",
		Stages: []Stage{{Index: 0, Difficulty: 1, Topics: []StageTopic{{Name: "react 入门"}}}},
	})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(outcome.Resources) != 0 {
		t.Fatalf("unscreened resources kept: %+v", outcome.Resources)
	}
	if outcome.Note == "" {
		t.Fatal("gate failure should explain itself")
	}
}

func TestWebSearcherNoProviderStaysEmpty(t *testing.T) {
	w := &WebSearcher{Provider: func() (LLM, error) { return nil, ErrNoLLM }}
	outcome, err := w.Search(context.Background(), ResourceQuery{Topic: "x"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(outcome.Resources) != 0 || outcome.Note == "" {
		t.Fatalf("outcome = %+v", outcome)
	}
}

func TestWebSearcherWithoutSelectionStaysEmpty(t *testing.T) {
	w := &WebSearcher{
		SearchModel: func() (provider.Provider, string, error) { return nil, "", nil },
		Provider:    func() (LLM, error) { return newScreenLLM(func(string) string { return "[]" }), nil },
	}
	outcome, err := w.Search(context.Background(), ResourceQuery{Topic: "x"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(outcome.Resources) != 0 || outcome.Note == "" || outcome.Engine != "" {
		t.Fatalf("outcome = %+v", outcome)
	}
}

func TestWebSearcherBrokenSelectionExplainsReason(t *testing.T) {
	w := &WebSearcher{
		SearchModel: func() (provider.Provider, string, error) {
			return nil, "", &config.WebSearchUnavailableError{Reason: config.WebSearchNoCredentials}
		},
		Provider: func() (LLM, error) { return newScreenLLM(func(string) string { return "[]" }), nil },
	}
	outcome, err := w.Search(context.Background(), ResourceQuery{Topic: "x"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(outcome.Resources) != 0 || !strings.Contains(outcome.Note, string(config.WebSearchNoCredentials)) {
		t.Fatalf("outcome = %+v", outcome)
	}
}

func TestSearchQuotas(t *testing.T) {
	if got := stageResourceCap(0); got != defaultStageCap {
		t.Errorf("stageResourceCap(0) = %d", got)
	}
	if got := stageResourceCap(5); got != hardStageCap {
		t.Errorf("stageResourceCap(5) = %d", got)
	}
	if got := totalResourceCap(100); got != hardTotalCap {
		t.Errorf("totalResourceCap(100) = %d", got)
	}
	if got := stageTopicCap(4); got != 4 {
		t.Errorf("stageTopicCap(4) = %d", got)
	}
}

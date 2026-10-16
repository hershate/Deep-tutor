package boot

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"testing"

	"reasonix/internal/contract/provider"
)

type overflowingSharedWindowProvider struct {
	mu      sync.Mutex
	refused int
	digests int
	maxSeen int
	limit   int
}

func (*overflowingSharedWindowProvider) Name() string              { return "boot-overflow" }
func (*overflowingSharedWindowProvider) SharesContextWindow() bool { return true }
func (*overflowingSharedWindowProvider) OutputBudget() int         { return 4_000 }

func (p *overflowingSharedWindowProvider) Stream(_ context.Context, req provider.Request) (<-chan provider.Chunk, error) {
	chars := 0
	for _, m := range req.Messages {
		chars += len(m.Content)
	}
	digest := len(req.Messages) > 0 && strings.Contains(req.Messages[0].Content, "compacting the earlier part")
	p.mu.Lock()
	defer p.mu.Unlock()
	if digest {
		p.digests++
	} else if chars > p.limit {
		p.refused++
		return nil, &provider.APIError{Provider: "boot-overflow", Status: http.StatusBadRequest,
			Body: `{"error":{"message":"This model's maximum context length is 20000 tokens. However, you requested more.","type":"invalid_request_error"}}`}
	} else {
		p.maxSeen = max(p.maxSeen, chars)
	}
	ch := make(chan provider.Chunk, 2)
	text := strings.Repeat("work output line with detail. ", 130)
	if digest {
		text = "## Summary\nearlier work folded"
	}
	ch <- provider.Chunk{Type: provider.ChunkText, Text: text}
	ch <- provider.Chunk{Type: provider.ChunkDone}
	close(ch)
	return ch, nil
}

func TestEffectCodelessOverflowOnASharedWindowFoldsAndReplays(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	prov := &overflowingSharedWindowProvider{limit: 18_000}
	kind := "boot-overflow-" + t.Name()
	provider.Register(kind, func(provider.Config) (provider.Provider, error) { return prov, nil })
	writeFile(t, dir, "reasonix.toml", `
default_model = "test-model"

[agent]
system_prompt = "BASE"
compact_ratio = 5

[[providers]]
name = "test-model"
kind = "`+kind+`"
model = "x"
context_window = 20000
`)
	approveWorkspace(t, dir)
	ctrl, err := Build(context.Background(), Options{Sink: &noticeSink{}})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer ctrl.Close()
	for i := range 20 {
		if err := ctrl.Run(context.Background(), "continue the work"); err != nil {
			t.Fatalf("turn %d failed instead of folding and replaying: %v (refused %d, digests %d)", i, err, prov.refused, prov.digests)
		}
		if prov.refused > 0 && prov.digests > 0 {
			return
		}
	}
	t.Fatalf("never reached a refusal that was folded: refused %d, digests %d, largest accepted %d chars", prov.refused, prov.digests, prov.maxSeen)
}

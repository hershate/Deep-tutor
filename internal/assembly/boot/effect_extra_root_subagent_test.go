package boot

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

// childWritesProvider scripts a parent that delegates and a child whose only
// actions are one write into the granted folder and one outside it.
type childWritesProvider struct {
	mu      sync.Mutex
	calls   int
	inside  string
	outside string
}

func (p *childWritesProvider) Name() string { return "boot-effect-child-writes" }

func (p *childWritesProvider) Stream(_ context.Context, _ provider.Request) (<-chan provider.Chunk, error) {
	p.mu.Lock()
	p.calls++
	n := p.calls
	p.mu.Unlock()
	ch := make(chan provider.Chunk, 3)
	write := func(id, path string) {
		args, _ := json.Marshal(map[string]string{"path": path, "content": "built\n"})
		ch <- provider.Chunk{Type: provider.ChunkToolCall, ToolCall: &provider.ToolCall{ID: id, Name: "write_file", Arguments: string(args)}}
	}
	switch n {
	case 1:
		args, _ := json.Marshal(map[string]any{"prompt": "write the files", "write_paths": []string{p.inside}})
		ch <- provider.Chunk{Type: provider.ChunkToolCall, ToolCall: &provider.ToolCall{ID: "t1", Name: "task", Arguments: string(args)}}
	case 2:
		write("w1", p.inside)
	case 3:
		write("w2", p.outside)
	default:
		ch <- provider.Chunk{Type: provider.ChunkText, Text: "ok"}
	}
	ch <- provider.Chunk{Type: provider.ChunkDone}
	close(ch)
	return ch, nil
}

// TestEffectDelegatedChildMayWriteTheGrantedFolderAndNothingBeyondIt runs a real
// delegated child through boot.Build: its write into the granted folder lands
// and is named in the turn's receipt, and its write to a folder nobody granted
// does not land.
func TestEffectDelegatedChildMayWriteTheGrantedFolderAndNothingBeyondIt(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	granted := extraRootFolder(t)
	outsideDir := extraRootFolder(t)
	inside := filepath.Join(granted, "built.txt")
	outside := filepath.Join(outsideDir, "stray.txt")
	provider.Register("boot-effect-child-writes", func(provider.Config) (provider.Provider, error) {
		return &childWritesProvider{inside: inside, outside: outside}, nil
	})
	writeFile(t, dir, "reasonix.toml", `
default_model = "test-model"

[agent]
system_prompt = "BASE"

[[providers]]
name = "test-model"
kind = "boot-effect-child-writes"
model = "x"
`)
	approveWorkspace(t, dir)
	sink := &childReceiptSink{done: make(chan struct{})}
	ctrl, err := Build(context.Background(), Options{Sink: sink})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer func() { ctrl.Close() }()
	if _, err := ctrl.AddWorkspaceRoot(granted); err != nil {
		t.Fatal(err)
	}
	ctrl.Close()
	if ctrl, err = Build(context.Background(), Options{Sink: sink}); err != nil {
		t.Fatalf("rebuild: %v", err)
	}
	ctrl.SetSessionPath(filepath.Join(dir, ".reasonix", "sessions", "child.jsonl"))
	ctrl.SetBypass(true)
	ctrl.Submit("delegate the writes")
	select {
	case <-sink.done:
	case <-time.After(90 * time.Second):
		t.Fatal("turn did not finish")
	}
	if _, err := os.Stat(inside); err != nil {
		t.Fatalf("the child's write into the granted folder did not land, so the delegation path was not exercised: %v", err)
	}
	if _, err := os.Stat(outside); err == nil {
		t.Fatal("the child wrote outside the granted folder")
	}
	sink.mu.Lock()
	defer sink.mu.Unlock()
	var receipt *event.CompletionReceipt
	for _, e := range sink.events {
		if e.Kind == event.TurnDone {
			receipt = e.Receipt
		}
	}
	if receipt == nil {
		t.Fatal("the turn carried no receipt")
	}
	for _, c := range receipt.Changes {
		if strings.HasSuffix(filepath.ToSlash(c.Path), "built.txt") {
			return
		}
	}
	t.Fatalf("the receipt does not name the granted-folder write: %+v", receipt)
}

type childReceiptSink struct {
	mu     sync.Mutex
	events []event.Event
	done   chan struct{}
	once   sync.Once
}

func (s *childReceiptSink) Emit(e event.Event) {
	s.mu.Lock()
	s.events = append(s.events, e)
	s.mu.Unlock()
	if e.Kind == event.TurnDone {
		s.once.Do(func() { close(s.done) })
	}
}

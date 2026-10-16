package plugin

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"testing"

	"reasonix/internal/contract/planmode"
	"reasonix/internal/contract/tool"
)

func metaOf(t *testing.T, params any) map[string]any {
	t.Helper()
	m, ok := params.(map[string]any)
	if !ok {
		t.Fatalf("params = %T", params)
	}
	meta, _ := m["_meta"].(map[string]any)
	return meta
}

// A local stdio server can be told where the workspace is so it can scope its
// own permissions; a remote one is told nothing, because the path would leave
// the machine.
func TestHostContextReachesOnlyLocalStdioServers(t *testing.T) {
	for _, c := range []struct {
		typ  string
		want bool
	}{
		{"", true}, {"stdio", true}, {"STDIO", true},
		{"http", false}, {"streamable-http", false}, {"streamable_http", false}, {"sse", false},
	} {
		client := &Client{spec: Spec{Type: c.typ, WorkspaceRoot: "/work/space"}}
		got := metaOf(t, client.withHostContext(context.Background(), "tools/call", map[string]any{"name": "t"}))[metaCwdKey]
		if (got == "/work/space") != c.want {
			t.Errorf("type %q: _meta[%s] = %v, want present=%v", c.typ, metaCwdKey, got, c.want)
		}
	}
}

func TestHostContextOnlyOnToolCallsAndTheWorkspaceOnlyWithARoot(t *testing.T) {
	client := &Client{spec: Spec{WorkspaceRoot: "/w"}}
	for _, method := range []string{"tools/list", "initialize", "resources/read"} {
		if meta := metaOf(t, client.withHostContext(context.Background(), method, map[string]any{})); meta != nil {
			t.Errorf("%s got _meta %v, want none", method, meta)
		}
	}
	meta := metaOf(t, (&Client{spec: Spec{}}).withHostContext(context.Background(), "tools/call", map[string]any{"name": "t"}))
	if _, has := meta[metaCwdKey]; has {
		t.Errorf("no workspace root still produced a cwd: %v", meta)
	}
	if _, has := meta[metaReadOnlyKey]; !has {
		t.Errorf("a local server without a root should still be told the host's posture: %v", meta)
	}
}

func TestHostContextKeepsWhatWasThereAndDoesNotTouchTheCallersMap(t *testing.T) {
	client := &Client{spec: Spec{WorkspaceRoot: "/w"}}
	in := map[string]any{"name": "t", "_meta": map[string]any{"other": 1, metaCwdKey: "server-set"}}
	out := client.withHostContext(context.Background(), "tools/call", in)
	meta := metaOf(t, out)
	if meta["other"] != 1 {
		t.Errorf("an existing _meta key was dropped: %v", meta)
	}
	if meta[metaCwdKey] != "server-set" {
		t.Errorf("a key already set was overwritten: %v", meta)
	}
	if in["_meta"].(map[string]any)[metaCwdKey] != "server-set" || len(in["_meta"].(map[string]any)) != 2 {
		t.Errorf("the caller's map was mutated: %v", in["_meta"])
	}
	plain := map[string]any{"name": "t"}
	client.withHostContext(context.Background(), "tools/call", plain)
	if _, ok := plain["_meta"]; ok {
		t.Errorf("the caller's params gained _meta")
	}
}

// What reaches the server on the wire, not just what the helper builds: a call
// through a real transport carries the workspace for a local server and nothing
// of the kind for a remote one, and the progress token is still there.
func TestToolCallOnTheWireCarriesTheWorkspaceOnlyForLocalServers(t *testing.T) {
	for _, c := range []struct {
		typ  string
		want bool
	}{{"stdio", true}, {"sse", false}} {
		serverReads, clientWrites := io.Pipe()
		clientReads, serverWrites := io.Pipe()
		tr := &stdioTransport{name: "w", stdin: clientWrites, stdout: bufio.NewReader(clientReads), stderr: &tailBuffer{limit: 1024}, pending: map[int]chan rpcResponse{}}
		go tr.readLoop()
		seen := make(chan map[string]any, 1)
		go func() {
			dec, enc := json.NewDecoder(serverReads), json.NewEncoder(serverWrites)
			var req struct {
				ID     int `json:"id"`
				Params struct {
					Meta map[string]any `json:"_meta"`
				} `json:"params"`
			}
			if dec.Decode(&req) != nil {
				return
			}
			seen <- req.Params.Meta
			_ = enc.Encode(map[string]any{"jsonrpc": "2.0", "id": req.ID, "result": map[string]any{"content": []any{}}})
		}()
		ctx := tool.WithProgress(context.Background(), func(string) {})
		client := &Client{name: "w", t: tr, spec: Spec{Type: c.typ, WorkspaceRoot: "/work/space"}}
		if _, err := client.call(ctx, "tools/call", map[string]any{"name": "t", "arguments": map[string]any{}}); err != nil {
			t.Fatalf("%s call: %v", c.typ, err)
		}
		meta := <-seen
		if got := meta[metaCwdKey]; (got == "/work/space") != c.want {
			t.Errorf("%s: wire _meta[%s] = %v, want present=%v", c.typ, metaCwdKey, got, c.want)
		}
		if meta["progressToken"] == nil {
			t.Errorf("%s: the progress token was lost: %v", c.typ, meta)
		}
		_ = clientWrites.Close()
		_ = serverReads.Close()
		_ = serverWrites.Close()
		_ = clientReads.Close()
	}
}

// Plan mode is the host refusing writes; a local server is told so it can refuse
// its own, and is told the opposite when the host is not.
func TestHostContextSaysWhetherTheHostIsRefusingWrites(t *testing.T) {
	client := &Client{spec: Spec{WorkspaceRoot: "/w"}}
	call := func(ctx context.Context) any {
		return metaOf(t, client.withHostContext(ctx, "tools/call", map[string]any{"name": "t"}))[metaReadOnlyKey]
	}
	if got := call(context.Background()); got != false {
		t.Errorf("outside plan mode _meta[%s] = %v, want false", metaReadOnlyKey, got)
	}
	if got := call(planmode.WithActive(context.Background(), true)); got != true {
		t.Errorf("in plan mode _meta[%s] = %v, want true", metaReadOnlyKey, got)
	}
	remote := &Client{spec: Spec{Type: "http", WorkspaceRoot: "/w"}}
	if meta := metaOf(t, remote.withHostContext(planmode.WithActive(context.Background(), true), "tools/call", map[string]any{"name": "t"})); meta != nil {
		t.Errorf("a remote server was told the host's posture: %v", meta)
	}
}

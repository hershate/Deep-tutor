package computer

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"reasonix/internal/base/proc"
)

// Driver owns one private CUA MCP process. A lost request is never replayed.
type Driver struct {
	path        string
	args        []string
	abortSocket string
	unavailable error
	env         []string
	mu          sync.Mutex
	desktop     sync.Mutex
	live        *driverProcess
	generation  uint64
}

type driverProcess struct {
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	replies chan []byte
	done    chan struct{}
	exited  chan struct{}
	next    int64
	tools   map[string]nativeToolSchema
}

type DriverContent struct {
	Type string `json:"type"`
	Text string `json:"text,omitempty"`
	Data string `json:"data,omitempty"`
	MIME string `json:"mimeType,omitempty"`
}

type DriverResult struct {
	Content    []DriverContent `json:"content"`
	Structured json.RawMessage `json:"structuredContent"`
	IsError    bool            `json:"isError"`
}

func NewDriver(path string, environment []string) *Driver {
	args := []string{"mcp"}
	if runtime.GOOS == "darwin" {
		args = append(args, "--direct")
	}
	return &Driver{path: path, env: append([]string(nil), environment...), args: args}
}

func NewEmbeddedDriver(path, socket, abortSocket string, environment []string) *Driver {
	h := &Driver{path: path, env: append([]string(nil), environment...), abortSocket: abortSocket, args: []string{"mcp", "--embedded", "--socket", socket, "--host-bundle-id", pinnedContract.Bundle}}
	if !filepath.IsAbs(socket) || !filepath.IsAbs(abortSocket) || filepath.Dir(socket) != filepath.Dir(abortSocket) {
		h.unavailable = fail(CodeUnavailable, "embedded CUA requires its private interruption endpoint")
	}
	return h
}

func (h *Driver) Close() {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.closeLocked()
}

func (h *Driver) closeLocked() {
	if h.live != nil {
		close(h.live.done)
		_ = h.live.stdin.Close()
		_ = h.live.cmd.Process.Kill()
		<-h.live.exited
		h.live = nil
	}
	h.generation++
}

func (h *Driver) Generation() uint64 {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.generation
}

func (h *Driver) start(ctx context.Context) error {
	if h.live != nil {
		return nil
	}
	if !filepath.IsAbs(h.path) {
		return fail(CodeUnavailable, "CUA executable must have an absolute path")
	}
	cmd := exec.Command(h.path, h.args...)
	for _, entry := range h.env {
		if !strings.HasPrefix(entry, "CUA_DRIVER_RS_TELEMETRY_ENABLED=") && !strings.HasPrefix(entry, "DO_NOT_TRACK=") {
			cmd.Env = append(cmd.Env, entry)
		}
	}
	cmd.Env = append(cmd.Env, "CUA_DRIVER_RS_TELEMETRY_ENABLED=false", "DO_NOT_TRACK=1")
	proc.HideWindow(cmd)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return fail(CodeUnavailable, "could not start CUA: %v", err)
	}
	p := &driverProcess{cmd: cmd, stdin: stdin, replies: make(chan []byte, 1), done: make(chan struct{}), exited: make(chan struct{})}
	h.live = p
	h.generation++
	go func() {
		defer func() { close(p.replies); _ = cmd.Process.Kill(); _ = cmd.Wait(); close(p.exited) }()
		sc := bufio.NewScanner(stdout)
		sc.Buffer(make([]byte, 64<<10), 32<<20)
		for sc.Scan() {
			select {
			case p.replies <- append([]byte(nil), sc.Bytes()...):
			case <-p.done:
				return
			}
		}
	}()
	var initialized struct {
		ServerInfo struct {
			Version string `json:"version"`
		} `json:"serverInfo"`
	}
	if err := h.rpc(ctx, "initialize", map[string]any{"protocolVersion": pinnedContract.Protocol, "capabilities": map[string]any{}, "clientInfo": map[string]string{"name": "reasonix", "version": "1"}}, &initialized); err != nil {
		return err
	}
	if initialized.ServerInfo.Version != pinnedContract.Version {
		return fail(CodeUnsupported, "CUA version %q; require %s", initialized.ServerInfo.Version, pinnedContract.Version)
	}
	if _, err = stdin.Write([]byte(`{"jsonrpc":"2.0","method":"notifications/initialized"}` + "\n")); err != nil {
		return err
	}
	return h.loadToolContract(ctx)
}

func (h *Driver) rpc(ctx context.Context, method string, params, out any) error {
	p := h.live
	p.next++
	payload, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": p.next, "method": method, "params": params})
	if err != nil {
		return err
	}
	if _, err := p.stdin.Write(append(payload, '\n')); err != nil {
		return fail(CodeFailed, "CUA connection closed")
	}
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case line, ok := <-p.replies:
			if !ok {
				return fail(CodeFailed, "CUA connection closed")
			}
			var reply struct {
				ID     int64           `json:"id"`
				Result json.RawMessage `json:"result"`
				Error  *struct {
					Code    int    `json:"code"`
					Message string `json:"message"`
				} `json:"error"`
			}
			if err := json.Unmarshal(line, &reply); err != nil {
				return fail(CodeFailed, "invalid CUA envelope")
			}
			if reply.ID == 0 {
				continue
			}
			if reply.ID != p.next {
				return fail(CodeFailed, "CUA response identity mismatch")
			}
			if reply.Error != nil {
				return fail(CodeFailed, "CUA RPC %d: %s", reply.Error.Code, reply.Error.Message)
			}
			return json.Unmarshal(reply.Result, out)
		}
	}
}

var driverTools = map[string]bool{
	"list_apps": true, "list_windows": true, "get_window_state": true, "launch_app": true,
	"click": true, "right_click": true, "set_value": true, "type_text": true, "press_key": true, "hotkey": true, "scroll": true, "drag": true, "move_cursor": true,
	"get_cursor_position": true, "end_session": true,
}

func (h *Driver) Call(ctx context.Context, name string, params map[string]any, out *DriverResult) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	if h.unavailable != nil {
		return h.unavailable
	}
	if !driverTools[name] {
		return fail(CodeUnsupported, "CUA tool %q is outside the computer adapter", name)
	}
	if err := h.start(ctx); err != nil {
		h.closeLocked()
		return err
	}
	arguments, err := h.live.arguments(name, params)
	if err != nil {
		return err
	}
	var result DriverResult
	if err := h.rpc(ctx, "tools/call", map[string]any{"name": name, "arguments": arguments}, &result); err != nil {
		h.closeLocked()
		if h.abortSocket != "" {
			if stopErr := abortEmbedded(h.abortSocket); stopErr != nil {
				h.unavailable = fail(CodeStopped, "embedded CUA stop could not be established; restart Studio: %v", stopErr)
				return h.unavailable
			}
		}
		return err
	}
	if err := checkDriverResult(result); err != nil {
		return err
	}

	if out != nil {
		*out = result
	}
	return nil
}

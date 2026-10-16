package plugin

import (
	"context"
	"maps"
	"strings"

	"reasonix/internal/contract/planmode"
)

// metaCwdKey is where a tool call tells a local server the workspace root. The
// key is namespaced as MCP asks of vendor keys in _meta.
const metaCwdKey = "io.reasonix/cwd"

// metaReadOnlyKey tells a local server whether the host is refusing writes right
// now (plan mode), so the server can refuse its own.
const metaReadOnlyKey = "io.reasonix/readOnly"

// withHostContext adds what a local stdio server may know about the host to a
// tool call. A remote server is told nothing: the workspace path would leave the
// machine, and a server nobody started here has no use for it. The caller's map
// is left alone, and a key the call already carries is never overwritten.
func (c *Client) withHostContext(ctx context.Context, method string, params any) any {
	if method != "tools/call" || canonicalMCPRuntimeTransport(c.spec.Type) != "stdio" {
		return params
	}
	call, ok := params.(map[string]any)
	if !ok {
		return params
	}
	next := maps.Clone(call)
	meta := map[string]any{}
	if existing, ok := call["_meta"].(map[string]any); ok {
		maps.Copy(meta, existing)
	}
	if _, set := meta[metaCwdKey]; !set && strings.TrimSpace(c.spec.WorkspaceRoot) != "" {
		meta[metaCwdKey] = c.spec.WorkspaceRoot
	}
	if _, set := meta[metaReadOnlyKey]; !set {
		meta[metaReadOnlyKey] = planmode.Active(ctx)
	}
	next["_meta"] = meta
	return next
}

package plugin

// ErrAmbiguousToolCatalog marks empty or colliding model-visible tool names.
var ErrAmbiguousToolCatalog error = hostDiagnosticCause("MCP tool catalog has empty or colliding names")

// maxModelToolName is Anthropic's documented limit on a tool name
// (^[a-zA-Z0-9_-]{1,128}$); one longer name makes the provider refuse the whole
// request, not just that tool.
const maxModelToolName = 128

// boundedToolName shortens tail so prefix+tail fits maxModelToolName, keeping
// the prefix and a hash of the full tail so siblings stay distinct. tail is
// normalised ASCII, so cutting by byte is safe; a prefix leaving no room for one
// character is returned as is.
func boundedToolName(prefix, tail string) string {
	if len(prefix)+len(tail) <= maxModelToolName {
		return prefix + tail
	}
	hash := shortNameHash(tail)
	keep := maxModelToolName - len(prefix) - len(hash) - 1
	if keep < 1 {
		return prefix + tail
	}
	return prefix + tail[:keep] + "_" + hash
}

func validateCachedToolNames(spec Spec, tools []CachedTool) error {
	names := make([]mcpTool, 0, len(tools))
	for _, ct := range tools {
		if spec.ToolEnabled(ct.Name) {
			names = append(names, mcpTool{Name: ct.Name})
		}
	}
	return validateMCPToolNames(spec.Name, spec.StripRawPrefix, names)
}

// tool_args.go — making a recorded tool-call argument string acceptable to a
// strict gateway before it is replayed.
package provider

import (
	"encoding/json"
	"slices"
	"strings"
)

// argsNeedRepair reports whether a recorded tool-call argument string would be
// refused by a strict gateway. An empty string is recorded as-is in a saved
// session (some gateways send "" for no-arg tools) but is not JSON, so it only
// needs repair on the way to a provider.
func argsNeedRepair(args string, forProvider bool) bool {
	if strings.TrimSpace(args) == "" {
		return forProvider
	}
	return !json.Valid([]byte(args))
}

func needsToolCallArgRepair(calls []ToolCall, forProvider bool) bool {
	for _, tc := range calls {
		if argsNeedRepair(tc.Arguments, forProvider) {
			return true
		}
	}
	return false
}

// repairToolCallArgs returns m with any undecodable tool-call Arguments closed
// into valid JSON (copy-on-write; the caller's history is never mutated). When
// the history is headed to a provider, empty arguments become an empty object.
func repairToolCallArgs(m Message, forProvider bool) Message {
	if !needsToolCallArgRepair(m.ToolCalls, forProvider) {
		return m
	}
	calls := make([]ToolCall, len(m.ToolCalls))
	copy(calls, m.ToolCalls)
	for i := range calls {
		if !argsNeedRepair(calls[i].Arguments, forProvider) {
			continue
		}
		if strings.TrimSpace(calls[i].Arguments) == "" {
			calls[i].Arguments = "{}"
			continue
		}
		calls[i].Arguments = closeTruncatedJSON(calls[i].Arguments)
	}
	m.ToolCalls = calls
	return m
}

// closeTruncatedJSON best-effort completes a JSON document cut off mid-stream
// (unterminated string, open braces, dangling comma/colon); anything still
// invalid after closing degrades to "{}".
func closeTruncatedJSON(s string) string {
	var stack []byte
	inStr, esc := false, false
	for i := range len(s) {
		c := s[i]
		if inStr {
			switch {
			case esc:
				esc = false
			case c == '\\':
				esc = true
			case c == '"':
				inStr = false
			}
			continue
		}
		switch c {
		case '"':
			inStr = true
		case '{':
			stack = append(stack, '}')
		case '[':
			stack = append(stack, ']')
		case '}', ']':
			if len(stack) > 0 {
				stack = stack[:len(stack)-1]
			}
		}
	}
	out := s
	if esc {
		out = out[:len(out)-1]
	}
	if inStr {
		out += `"`
	}
	trimmed := strings.TrimRight(out, " \t\r\n")
	switch {
	case strings.HasSuffix(trimmed, ","):
		out = trimmed[:len(trimmed)-1]
	case strings.HasSuffix(trimmed, ":"):
		out = trimmed + "null"
	}
	for _, v := range slices.Backward(stack) {
		out += string(v)
	}
	if !json.Valid([]byte(out)) {
		return "{}"
	}
	return out
}

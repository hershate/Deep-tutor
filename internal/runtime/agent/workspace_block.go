package agent

import (
	"reasonix/internal/runtime/langpref"
	"strconv"
	"strings"
)

// WorkspaceBlock renders the transient block carrying this turn's per-project
// facts. They ride the turn rather than the system prompt because every byte
// after the first per-project one — the rest of the prompt and every tool
// schema — misses the provider's prefix cache on a project the machine has not
// warmed. Keep new per-project facts here, not in the prefix.
func WorkspaceBlock(root, vcs string, extra []string) string {
	if root == "" {
		return ""
	}
	if vcs == "" {
		vcs = "none (not a repository)"
	}
	// Version control is stated either way. Left unsaid, a model reaches for
	// `git diff` to review its own work and burns a round finding out.
	block := "<workspace>\nCurrent workspace: " + strconv.Quote(root) +
		". Shell commands start there and every tool resolves relative paths from it.\n" +
		"Version control: " + vcs + ".\n"
	if len(extra) > 0 {
		quoted := make([]string, len(extra))
		for i, dir := range extra {
			quoted[i] = strconv.Quote(dir)
		}
		block += "Additional folders granted to this workspace, writable like it (use absolute paths): " + strings.Join(quoted, ", ") + ".\n"
	}
	return block + "</workspace>"
}

// WithWorkspace prefixes content with the transient workspace block unless the
// turn already starts with one.
func WithWorkspace(content, root, vcs string, extra []string) string {
	block := WorkspaceBlock(root, vcs, extra)
	if block == "" || langpref.HasLeadingInjectedBlock(content, "workspace") {
		return content
	}
	return block + "\n\n" + content
}

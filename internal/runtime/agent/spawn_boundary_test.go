package agent

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// knownDirectChildRunners are the entry points that still construct a child
// outside TaskTool.RunProfileSpec. Each one re-resolves tools, depth, and
// permissions on its own, which is exactly how a boundary added in one place
// gets missed in another. The list may shrink; adding to it needs a reason in
// the pull request.
var knownDirectChildRunners = map[string]string{
	"internal/runtime/agent/child_run.go":      "defines the runners",
	"internal/runtime/delegation/task.go":      "the unified path itself",
	"internal/assembly/boot/skill_subagent.go": "run_skill + read_only_skill runners",
	"internal/frontend/cli/review.go":          "deep-tutor review",
	"desktop/subagents_app.go":                 "desktop profile preview",
}

// repositoryGoFiles lists the Go files this repository holds: the ones git
// tracks, and the new ones git does not ignore. A local copy of the source
// (REF/) is not among them — it is not part of this repository, a fork there
// is nobody's change, and reading it made this guard fail for a tree nobody
// edits.
func repositoryGoFiles(t *testing.T, root string) []string {
	t.Helper()
	cmd := exec.Command("git", "ls-files", "--cached", "--others", "--exclude-standard")
	cmd.Dir = root
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("list repository files: %v", err)
	}
	var files []string
	for rel := range strings.SplitSeq(strings.TrimSpace(string(out)), "\n") {
		rel = strings.TrimSpace(rel)
		if !strings.HasSuffix(rel, ".go") || strings.HasSuffix(rel, "_test.go") {
			continue
		}
		files = append(files, filepath.Join(root, filepath.FromSlash(rel)))
	}
	return files
}

// A new fork must be a deliberate, reviewed choice rather than something that
// appears because one more caller found the low-level runner convenient.
func TestChildConstructionForksStayEnumerated(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	var found []string
	for _, path := range repositoryGoFiles(t, root) {
		body, readErr := os.ReadFile(path)
		if readErr != nil {
			// A tracked file deleted from the working tree is not a fork.
			continue
		}
		text := string(body)
		if !strings.Contains(text, "RunSubAgentWithSession(") && !strings.Contains(text, "RunReadOnlySubAgentWithSession(") {
			continue
		}
		rel, relErr := filepath.Rel(root, path)
		if relErr != nil {
			t.Fatal(relErr)
		}
		rel = filepath.ToSlash(rel)
		if _, known := knownDirectChildRunners[rel]; !known {
			found = append(found, rel)
		}
	}
	if len(found) > 0 {
		t.Fatalf("these files construct a sub-agent outside the unified runner: %s\n\nCompile the call into a ProfileExecSpec and run it through TaskTool.RunProfileSpec so it inherits tool scoping, depth caps, write claims, scheduler slots, and the completion contract. If a direct runner is genuinely required, add the file to knownDirectChildRunners with a reason.",
			strings.Join(found, ", "))
	}
}

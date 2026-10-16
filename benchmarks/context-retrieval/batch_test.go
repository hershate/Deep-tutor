package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func seedArtifacts(t *testing.T, root string) {
	t.Helper()
	for _, name := range []string{"results.jsonl", "trajectories.jsonl"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("{}\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func readManifest(t *testing.T, root string) (batchManifest, string) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(root, "batch.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got batchManifest
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	return got, string(data)
}

func TestBatchManifestRecordsWhatTheBatchWas(t *testing.T) {
	root := t.TempDir()
	seedArtifacts(t, root)
	all := []contextMetrics{
		{Task: "b", Arm: "index-off"},
		{Task: "a", Arm: "index-off"},
		{Task: "a", Arm: "index-half", UsageReportedRequests: 2, PromptTokens: 100,
			CompletionTokens: 10, CacheHitTokens: 60, CacheMissTokens: 40},
	}
	if err := writeBatchManifest(root, batchInfo{Mode: "run-index", Planned: 4, Started: time.Now().Add(-time.Minute)}, all); err != nil {
		t.Fatal(err)
	}
	got, _ := readManifest(t, root)
	if got.SchemaVersion != 1 || got.Mode != "run-index" || got.PlannedRuns != 4 || got.CompletedRuns != 3 {
		t.Fatalf("manifest header = %+v", got)
	}
	if strings.Join(got.TaskIDs, ",") != "a,b" || strings.Join(got.Arms, ",") != "index-half,index-off" {
		t.Fatalf("ids/arms = %v / %v, want sorted and unique", got.TaskIDs, got.Arms)
	}
	if got.UsageReportedRuns != 1 || got.UsageReportedRequests != 2 || got.PromptTokens != 100 ||
		got.CompletionTokens != 10 || got.CacheHitTokens != 60 || got.CacheMissTokens != 40 {
		t.Fatalf("usage totals = %+v", got)
	}
	// No repo means git could not describe the checkout: unknown commit, and no
	// dirty flag at all, because "clean" is a claim this batch cannot make.
	if got.GitCommit != "unknown" || got.GitDirty != nil {
		t.Fatalf("git state = %q / %v, want unknown and no dirty flag", got.GitCommit, got.GitDirty)
	}
	want := fmt.Sprintf("%x", sha256.Sum256([]byte("{}\n")))
	if got.ResultsSHA256 != want || got.TrajectoriesSHA256 != want {
		t.Fatalf("digests = %q / %q, want %q", got.ResultsSHA256, got.TrajectoriesSHA256, want)
	}
}

// A dry batch pays nothing, so its cost fields must be absent rather than zero:
// an unmeasured spend must not read as a measured one.
func TestBatchManifestLeavesUnreportedUsageAbsent(t *testing.T) {
	root := t.TempDir()
	seedArtifacts(t, root)
	all := []contextMetrics{{Task: "a", Arm: "index-off"}, {Task: "a", Arm: "index-half"}}
	if err := writeBatchManifest(root, batchInfo{Mode: "run-index", Planned: 2, Started: time.Now(), Dry: true}, all); err != nil {
		t.Fatal(err)
	}
	got, raw := readManifest(t, root)
	if got.UsageReportedRuns != 0 {
		t.Fatalf("usage_reported_runs = %d, want 0 reported", got.UsageReportedRuns)
	}
	if got.Model != "scripted" {
		t.Fatalf("dry batch model = %q, want scripted", got.Model)
	}
	for _, absent := range []string{"usage_reported_requests", "prompt_tokens", "completion_tokens", "cache_hit_tokens"} {
		if strings.Contains(raw, absent) {
			t.Fatalf("a dry batch wrote %q:\n%s", absent, raw)
		}
	}
}

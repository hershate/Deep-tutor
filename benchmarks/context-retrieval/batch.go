package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// batchInfo is what the mode itself knows before any run happens. Everything
// else in the manifest is derived from the runs that were written, so a figure
// in it cannot drift from the artifact beside it.
type batchInfo struct {
	Mode    string
	Planned int
	Started time.Time
	Dry     bool
	// Repo is the checkout the batch ran from, captured before the harness
	// isolates its workspace: git cannot describe a directory that is gone.
	Repo string
}

// batchManifest is written beside results.jsonl so a later reader does not have
// to reconstruct what the batch was from a terminal scrollback.
type batchManifest struct {
	SchemaVersion int      `json:"schema_version"`
	Mode          string   `json:"mode"`
	StartedAt     string   `json:"started_at"`
	FinishedAt    string   `json:"finished_at"`
	GitCommit     string   `json:"git_commit"`
	GitDirty      *bool    `json:"git_dirty,omitempty"`
	Model         string   `json:"model"`
	Window        int      `json:"window"`
	Generations   int      `json:"generations"`
	TaskIDs       []string `json:"task_ids"`
	Arms          []string `json:"arms"`
	PlannedRuns   int      `json:"planned_runs"`
	CompletedRuns int      `json:"completed_runs"`
	// Usage totals are absent when nothing reported any: an unmeasured cost must
	// not read as a measured zero. UsageReportedRuns stays, because zero there
	// is a fact about the batch.
	UsageReportedRuns     int    `json:"usage_reported_runs"`
	UsageReportedRequests int    `json:"usage_reported_requests,omitempty"`
	PromptTokens          int    `json:"prompt_tokens,omitempty"`
	CompletionTokens      int    `json:"completion_tokens,omitempty"`
	CacheHitTokens        int    `json:"cache_hit_tokens,omitempty"`
	CacheMissTokens       int    `json:"cache_miss_tokens,omitempty"`
	UsageEstimated        bool   `json:"usage_estimated,omitempty"`
	ResultsSHA256         string `json:"results_sha256"`
	TrajectoriesSHA256    string `json:"trajectories_sha256"`
}

// gitCommitAndDirty reports the checkout the batch ran from. Unknown is written
// rather than omitted, so an absent field is never mistaken for a clean tree.
func gitCommitAndDirty(repo string) (commit string, dirty *bool) {
	commit = "unknown"
	if repo == "" {
		return commit, nil
	}
	in := func(args ...string) ([]byte, error) {
		cmd := exec.Command("git", args...)
		cmd.Dir = repo
		return cmd.Output()
	}
	if out, err := in("rev-parse", "--short", "HEAD"); err == nil {
		commit = strings.TrimSpace(string(out))
	}
	if out, err := in("status", "--porcelain"); err == nil {
		state := strings.TrimSpace(string(out)) != ""
		dirty = &state
	}
	return commit, dirty
}

func fileSHA256(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", sha256.Sum256(data)), nil
}

func sortedKeys(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// writeBatchManifest records the batch beside the runs it describes. It runs
// last, so its digests are of what is already on disk.
func writeBatchManifest(root string, info batchInfo, all []contextMetrics) error {
	ids, arms := map[string]bool{}, map[string]bool{}
	var prompt, completion, cacheHit, cacheMiss, requests, reported int
	estimated := false
	for _, m := range all {
		ids[m.Task] = true
		arms[m.Arm] = true
		if m.UsageReportedRequests > 0 {
			reported++
			requests += m.UsageReportedRequests
			prompt += m.PromptTokens
			completion += m.CompletionTokens
			cacheHit += m.CacheHitTokens
			cacheMiss += m.CacheMissTokens
			estimated = estimated || m.UsageEstimated
		}
	}
	commit, dirty := gitCommitAndDirty(info.Repo)
	model := runModel
	if info.Dry {
		model = "scripted"
	}
	manifest := batchManifest{
		SchemaVersion: 1, Mode: info.Mode,
		StartedAt:  info.Started.UTC().Format(time.RFC3339),
		FinishedAt: time.Now().UTC().Format(time.RFC3339),
		GitCommit:  commit, GitDirty: dirty,
		Model: model, Window: fixtureWindow, Generations: fixtureGenerations,
		TaskIDs: sortedKeys(ids), Arms: sortedKeys(arms),
		PlannedRuns: info.Planned, CompletedRuns: len(all),
		UsageReportedRuns: reported, UsageReportedRequests: requests,
		PromptTokens: prompt, CompletionTokens: completion,
		CacheHitTokens: cacheHit, CacheMissTokens: cacheMiss, UsageEstimated: estimated,
	}
	var err error
	if manifest.ResultsSHA256, err = fileSHA256(filepath.Join(root, "results.jsonl")); err != nil {
		return err
	}
	if manifest.TrajectoriesSHA256, err = fileSHA256(filepath.Join(root, "trajectories.jsonl")); err != nil {
		return err
	}
	data, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(root, "batch.json"), append(data, '\n'), 0o644)
}

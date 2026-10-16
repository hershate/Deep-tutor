package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
)

func TestSFTRejectsAMixedBatchBeforeReplacingTheOutput(t *testing.T) {
	dir := testenv.TempDir(t)
	writeTraj(t, dir, "fizzbuzz", trajHeader, `{"event":{"kind":"message","text":"failed attempt"}}`, trajUsage)
	report := filepath.Join(dir, "report.json")
	if err := os.WriteFile(report, []byte(`[{"ID":"fizzbuzz","Passed":true,"sft":{"trajectory_sha256":"another-run","task_sha256":"another-corpus","prompt":"another prompt"}}]`), 0o644); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(dir, "sft.jsonl")
	if err := os.WriteFile(out, []byte("existing export\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := runSFTMode(dir, corpusDir, report, out); err == nil {
		t.Fatal("a verdict from another run labelled this failed trajectory as passed")
	}
	got, err := os.ReadFile(out)
	if err != nil || string(got) != "existing export\n" {
		t.Fatalf("refused export replaced the existing output: %q, %v", got, err)
	}
}

func gradedSFTResult(t *testing.T, dir string, task task, passed bool) result {
	t.Helper()
	source, err := newSFTProvenance(task)
	if err != nil {
		t.Fatal(err)
	}
	if err := bindSFTTrajectory(filepath.Join(dir, task.ID+".trajectory.jsonl"), source); err != nil {
		t.Fatal(err)
	}
	return result{task: task, Passed: passed, SFT: source}
}

func sftFixture(t *testing.T) (dir, suite, report string, rows []result) {
	t.Helper()
	dir = testenv.TempDir(t)
	suite = filepath.Join(dir, "suite")
	taskDir := filepath.Join(suite, "tasks", "demo")
	if err := os.MkdirAll(filepath.Join(taskDir, "workdir"), 0o755); err != nil {
		t.Fatal(err)
	}
	for name, body := range map[string]string{"task.toml": "prompt = 'original prompt'\n", "verify.sh": "exit 0\n", "workdir/seed.txt": "original seed"} {
		if err := os.WriteFile(filepath.Join(taskDir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	tasks, err := loadTasks(suite)
	if err != nil {
		t.Fatal(err)
	}
	actual := tasks[0]
	actual.Prompt = "planner directive\n\n" + actual.Prompt
	writeTraj(t, dir, "demo", trajHeader, `{"event":{"kind":"message","text":"done"}}`, trajUsage)
	rows = []result{gradedSFTResult(t, dir, actual, true)}
	report = filepath.Join(dir, "report.json")
	writeSFTReport(t, report, rows)
	return
}

func writeSFTReport(t *testing.T, path string, rows []result) {
	t.Helper()
	raw, err := json.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestSFTUsesTheRecordedPromptAndAValidatedSnapshot(t *testing.T) {
	dir, suite, report, rows := sftFixture(t)
	path := filepath.Join(dir, "demo.trajectory.jsonl")
	inputs, err := sftInputs([]string{path}, suite, report)
	if err != nil {
		t.Fatal(err)
	}
	var captured sftInput
	count := 0
	for input, err := range inputs {
		if err != nil {
			t.Fatal(err)
		}
		captured = input
		count++
	}
	if count != 1 {
		t.Fatalf("got %d inputs", count)
	}
	if err := os.WriteFile(path, []byte("a later run replaced this file"), 0o600); err != nil {
		t.Fatal(err)
	}
	sample, why := buildSFTSampleRecords(captured.id, captured.prompt, captured.records)
	if sample == nil || sample.Messages[1].Content != rows[0].SFT.Prompt {
		t.Fatalf("lost captured prompt or trajectory: %+v, %s", sample, why)
	}
}

func TestSFTRejectsChangedTaskInputs(t *testing.T) {
	for _, file := range []string{"task.toml", "verify.sh", "workdir/seed.txt"} {
		t.Run(file, func(t *testing.T) {
			dir, suite, report, _ := sftFixture(t)
			if err := os.WriteFile(filepath.Join(suite, "tasks", "demo", file), []byte("prompt = 'changed'\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			err := runSFTMode(dir, suite, report, filepath.Join(dir, "out.jsonl"))
			if err == nil || !strings.Contains(err.Error(), "task files differ") {
				t.Fatalf("changed %s accepted: %v", file, err)
			}
		})
	}
}

func TestSFTBindsRepeatedAttemptsToTheirOwnVerdicts(t *testing.T) {
	dir, suite, report, rows := sftFixture(t)
	writeTraj(t, dir, "demo", trajHeader, `{"event":{"kind":"message","text":"done"}}`, trajUsage)
	failed := gradedSFTResult(t, dir, rows[0].task, false)
	if failed.SFT.RunID == rows[0].SFT.RunID || failed.SFT.TrajectorySHA256 == rows[0].SFT.TrajectorySHA256 {
		t.Fatal("identical event streams from separate attempts share an identity")
	}
	writeSFTReport(t, report, append(rows, failed))
	out := filepath.Join(dir, "out.jsonl")
	if err := runSFTMode(dir, suite, report, out); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(out)
	if err != nil || len(data) != 0 {
		t.Fatalf("the latest failed attempt borrowed a passing verdict: %q, %v", data, err)
	}
}

func TestSFTRefusesLegacyAndDuplicateReports(t *testing.T) {
	for _, kind := range []string{"legacy", "duplicate", "header"} {
		t.Run(kind, func(t *testing.T) {
			dir, suite, report, rows := sftFixture(t)
			switch kind {
			case "legacy":
				rows[0].SFT = nil
			case "duplicate":
				rows = append(rows, rows[0])
			case "header":
				rows[0].SFT.RunID = "another-run"
			}
			writeSFTReport(t, report, rows)
			if err := runSFTMode(dir, suite, report, filepath.Join(dir, "out.jsonl")); err == nil {
				t.Fatal("unverifiable report accepted")
			}
		})
	}
}

func TestRunTaskProducesProvenanceThatSFTCanValidate(t *testing.T) {
	requireShellStub(t)
	dir, suite, report, _ := sftFixture(t)
	tasks, err := loadTasks(suite)
	if err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(dir, "agent")
	script := "#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do\ncase \"$1\" in\n" +
		"--trajectory) printf '%s\\n' '" + trajHeader + "' '" + trajUsage + "' '{\"event\":{\"kind\":\"message\",\"text\":\"done\"}}' > \"$2\"; shift 2;;\n" +
		"--metrics) printf '%s\\n' '{\"steps\":1,\"complete\":true}' > \"$2\"; shift 2;;\n*) shift;;\nesac\ndone\n"
	if err := os.WriteFile(bin, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	r := runTask(suiteConfig{bin: bin, trajDir: dir, forcePlanner: true}, tasks[0])
	if !r.Passed || r.SFT == nil || r.SFT.TrajectorySHA256 == "" || r.Trajectory == nil {
		t.Fatalf("runner lost provenance, grader, or digest: %+v", r)
	}
	writeSFTReport(t, report, []result{r})
	out := filepath.Join(dir, "out.jsonl")
	if err := runSFTMode(dir, suite, report, out); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	var sample sftSample
	if err := json.Unmarshal(raw, &sample); err != nil {
		t.Fatal(err)
	}
	if sample.Messages[1].Content != r.SFT.Prompt || !r.PlanForced || sample.Messages[1].Content == tasks[0].Prompt {
		t.Fatal("export did not use the prompt actually passed to the child")
	}
}

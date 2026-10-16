package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"iter"
	"os"
	"path/filepath"
)

type sftProvenance struct {
	RunID            string `json:"run_id"`
	TaskSHA256       string `json:"task_sha256"`
	Prompt           string `json:"prompt"`
	TrajectorySHA256 string `json:"trajectory_sha256,omitempty"`
}

func newSFTProvenance(t task) (*sftProvenance, error) {
	digest, err := sftTaskDigest(t)
	if err != nil {
		return nil, err
	}
	return &sftProvenance{RunID: rand.Text(), TaskSHA256: digest, Prompt: t.Prompt}, nil
}

func sftTaskDigest(t task) (string, error) {
	h := sha256.New()
	enc := json.NewEncoder(h)
	err := filepath.WalkDir(t.dir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == t.dir {
			return nil
		}
		rel, err := filepath.Rel(t.dir, path)
		if err != nil {
			return err
		}
		if rel == "solution" && d.IsDir() {
			return filepath.SkipDir
		}
		if d.IsDir() {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("task input is not a regular file: %s", path)
		}
		digest, err := sftFileDigest(path)
		if err != nil {
			return err
		}
		return enc.Encode(struct {
			Path       string
			Executable uint32
			Digest     string
		}{filepath.ToSlash(rel), uint32(info.Mode().Perm() & 0o111), digest})
	})
	if err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func sftFileDigest(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// The harness binds its inputs after the child closes the trajectory. Event
// bytes stay untouched; the unique run header makes repeated attempts distinct.
func bindSFTTrajectory(path string, source *sftProvenance) error {
	in, err := os.Open(path)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.CreateTemp(filepath.Dir(path), ".sft-trajectory-*")
	if err != nil {
		return err
	}
	defer os.Remove(out.Name())
	defer out.Close()
	header := *source
	header.TrajectorySHA256 = ""
	if err := json.NewEncoder(out).Encode(struct {
		Source sftProvenance `json:"benchmark_run"`
	}{header}); err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		return err
	}
	if err := in.Close(); err != nil {
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	if err := os.Rename(out.Name(), path); err != nil {
		return err
	}
	source.TrajectorySHA256, err = sftFileDigest(path)
	return err
}

type sftInput struct {
	id, prompt string
	records    []sftRecord
}

func sftInputs(paths []string, suite, report string) (iter.Seq2[sftInput, error], error) {
	tasks, err := loadTasks(suite)
	if err != nil {
		return nil, err
	}
	byTask := make(map[string]task, len(tasks))
	for _, t := range tasks {
		byTask[t.ID] = t
	}
	verdicts, err := sftVerdicts(report)
	if err != nil {
		return nil, err
	}
	return func(yield func(sftInput, error) bool) {
		for _, path := range paths {
			input, err := sftInputFor(path, suite, byTask, verdicts)
			if err != nil {
				yield(sftInput{}, err)
				return
			}
			if input != nil && !yield(*input, nil) {
				return
			}
		}
	}, nil
}

func sftInputFor(path, suite string, tasks map[string]task, verdicts map[string]map[string]result) (*sftInput, error) {
	id := filepath.Base(path)
	id = id[:len(id)-len(".trajectory.jsonl")]
	records, digest, err := readSFTSnapshot(path)
	if err != nil {
		return nil, err
	}
	r, ok := verdicts[id][digest]
	if !ok {
		return nil, fmt.Errorf("%s: report does not identify this trajectory; regenerate the report and trajectories together", id)
	}
	t, ok := tasks[id]
	if !ok {
		return nil, fmt.Errorf("%s: task is absent from %s", id, suite)
	}
	taskDigest, err := sftTaskDigest(t)
	if err != nil {
		return nil, err
	}
	if taskDigest != r.SFT.TaskSHA256 {
		return nil, fmt.Errorf("%s: task files differ from the graded run", id)
	}
	var source *sftProvenance
	for _, rec := range records {
		if rec.BenchmarkRun == nil {
			continue
		}
		if source != nil {
			return nil, fmt.Errorf("%s: multiple benchmark run headers", id)
		}
		source = rec.BenchmarkRun
	}
	if source == nil || source.RunID != r.SFT.RunID || source.TaskSHA256 != r.SFT.TaskSHA256 || source.Prompt != r.SFT.Prompt {
		return nil, fmt.Errorf("%s: benchmark run header does not match the report", id)
	}
	if !r.Passed || r.Skipped {
		return nil, nil
	}
	return &sftInput{id, source.Prompt, records}, nil
}

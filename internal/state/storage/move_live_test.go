package storage

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
)

func appendByte(t *testing.T, path string) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.Write([]byte{'x'}); err != nil {
		t.Fatal(err)
	}
}

func liveSeed(t *testing.T) (home string, files []string) {
	t.Helper()
	home = stateHome(t)
	files = []string{
		filepath.Join(home, "sessions", "a.jsonl"),
		filepath.Join(home, "sessions", "b.jsonl"),
		filepath.Join(home, "archive", "old.jsonl"),
	}
	for _, f := range files {
		write(t, f, 2048)
	}
	return home, files
}

// Proving the copy walks every file, and a person watching a large move has to
// see that pass advance rather than a single frozen message.
func TestVerifyingReportsProgressFileByFile(t *testing.T) {
	home, _ := liveSeed(t)
	_ = home
	target := filepath.Join(testenv.TempDir(t), "moved")
	plan := PlanMove(t.Context(), config.RootState, target)
	var verifying []Progress
	if err := Move(t.Context(), plan, func(p Progress) {
		if p.Phase == PhaseVerifying {
			verifying = append(verifying, p)
		}
	}); err != nil {
		t.Fatalf("move: %v", err)
	}
	if len(verifying) < 3 {
		t.Fatalf("verifying reported %d times for 3 files, want one per file", len(verifying))
	}
	last := verifying[len(verifying)-1]
	if last.Files != 3 || last.Bytes < 3*2048 {
		t.Fatalf("last verifying report = %+v, want 3 files and all their bytes", last)
	}
}

// A running process writes to the old location while it is copied. What it
// wrote after the first pass is copied again instead of failing the move.
func TestWritesDuringTheCopyAreCaughtUpBeforeTheCommit(t *testing.T) {
	home, files := liveSeed(t)
	target := filepath.Join(testenv.TempDir(t), "moved")
	plan := PlanMove(t.Context(), config.RootState, target)
	wrote := false
	if err := Move(t.Context(), plan, func(p Progress) {
		if p.Phase == PhaseVerifying && !wrote && p.Detail == "" {
			wrote = true
			for _, f := range files {
				appendByte(t, f)
			}
			write(t, filepath.Join(home, "sessions", "new.jsonl"), 512)
		}
	}); err != nil {
		t.Fatalf("a move over a source that changed mid-copy failed: %v", err)
	}
	for _, rel := range []string{"sessions/a.jsonl", "sessions/b.jsonl", "archive/old.jsonl"} {
		got, err := os.ReadFile(filepath.Join(target, filepath.FromSlash(rel)))
		if err != nil {
			t.Fatalf("%s missing from the copy: %v", rel, err)
		}
		if len(got) != 2049 || !bytes.HasSuffix(got, []byte{'x'}) {
			t.Fatalf("%s holds %d bytes, want the 2049 the source ended with", rel, len(got))
		}
	}
	if _, err := os.Stat(filepath.Join(target, "sessions", "new.jsonl")); err != nil {
		t.Fatalf("a file created during the copy was not carried over: %v", err)
	}
}

// A source that never stops changing is a refusal with an identity, and it
// changes nothing: the runtime stays on the data it has.
func TestASourceThatNeverSettlesRefusesTheMove(t *testing.T) {
	home, files := liveSeed(t)
	target := filepath.Join(testenv.TempDir(t), "moved")
	plan := PlanMove(t.Context(), config.RootState, target)
	err := Move(t.Context(), plan, func(p Progress) {
		if p.Phase == PhaseVerifying && p.Detail == "" {
			for _, f := range files {
				appendByte(t, f)
			}
		}
	})
	if !errors.Is(err, ErrSourceChanging) {
		t.Fatalf("err = %v, want ErrSourceChanging", err)
	}
	if got := config.RootDir(config.RootState); got != home {
		t.Fatalf("state resolves to %q after a refused move, want %q", got, home)
	}
	if _, statErr := os.Stat(files[0]); statErr != nil {
		t.Fatalf("the source was touched by a refused move: %v", statErr)
	}
}

// The runtime keeps writing to the old location until it restarts, so a file
// written after the copy was proved exists nowhere else and is not deleted.
func TestAFileWrittenAfterTheProofSurvivesTheReclaim(t *testing.T) {
	home, files := liveSeed(t)
	target := filepath.Join(testenv.TempDir(t), "moved")
	plan := PlanMove(t.Context(), config.RootState, target)
	var detail string
	if err := Move(t.Context(), plan, func(p Progress) {
		if p.Phase == PhaseCommitted {
			appendByte(t, files[0])
		}
		if p.Phase == PhaseDone {
			detail = p.Detail
		}
	}); err != nil {
		t.Fatalf("move: %v", err)
	}
	got, err := os.ReadFile(files[0])
	if err != nil || len(got) != 2049 {
		t.Fatalf("the late write was lost: err=%v len=%d", err, len(got))
	}
	if !strings.Contains(detail, "kept") {
		t.Fatalf("done detail = %q, want it to say the old folder was kept", detail)
	}
	_ = home
}

// A refused target names what is in the way and what the data would own, so the
// refusal can be acted on instead of guessed at.
func TestARefusedTargetNamesWhatIsInTheWay(t *testing.T) {
	liveSeed(t)
	target := testenv.TempDir(t)
	for _, name := range []string{"sessions", "cache", "config.toml"} {
		write(t, filepath.Join(target, name, "f"), 8)
	}
	plan := PlanMove(t.Context(), config.RootState, target)
	var detail string
	for _, r := range plan.Refusals {
		if r.Code == "target.not_empty" {
			detail = r.Detail
		}
	}
	if detail == "" {
		t.Fatalf("no target.not_empty refusal in %+v", plan.Refusals)
	}
	for _, want := range []string{"cache", "config.toml", "sessions"} {
		if !strings.Contains(detail, want) {
			t.Fatalf("detail %q does not mention %q", detail, want)
		}
	}
	if strings.Count(detail, "cache") != 1 || !strings.Contains(detail, "does not own") {
		t.Fatalf("detail %q should list cache once as something this data does not own", detail)
	}
}

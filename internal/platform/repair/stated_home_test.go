package repair

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
)

// TestRecordHealthyConfigForKeepsEachHomeItsOwnSnapshot proves the snapshot
// belongs to the home the config was read from. A process serving two users
// must not keep one user's config under the other's name: the second user's
// recovery would then restore the first user's settings.
func TestRecordHealthyConfigForKeepsEachHomeItsOwnSnapshot(t *testing.T) {
	processHome := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", processHome)
	t.Setenv("DEEPTUTOR_HOME", processHome)
	t.Setenv("DEEPTUTOR_STATE_HOME", "")

	home := testenv.TempDir(t)
	body := []byte("default_model = \"deepseek-flash\"\n")
	if err := os.WriteFile(filepath.Join(home, "config.toml"), body, 0o600); err != nil {
		t.Fatal(err)
	}

	roots := config.RootsForHome(home)
	if err := RecordHealthyConfigFor(roots, "v1"); err != nil {
		t.Fatal(err)
	}

	snapshot := roots.LastKnownGoodConfigPath()
	if snapshot == "" {
		t.Fatal("the stated home has no last-known-good path")
	}
	got, err := os.ReadFile(snapshot)
	if err != nil {
		t.Fatalf("read the stated home snapshot: %v", err)
	}
	if string(got) != string(body) {
		t.Fatalf("snapshot = %q, want %q", got, body)
	}

	// The series of immutable snapshots belongs to that home as well.
	snapshots, err := ListConfigSnapshotsFor(roots)
	if err != nil {
		t.Fatalf("list the stated home snapshots: %v", err)
	}
	if len(snapshots) != 1 {
		t.Fatalf("stated home snapshots = %d, want 1", len(snapshots))
	}
	if !strings.HasPrefix(snapshots[0].Path, home) {
		t.Fatalf("snapshot %s is not under the stated home %s", snapshots[0].Path, home)
	}

	// The process's own home must not have gained the user's snapshot.
	if processSnapshot := config.LastKnownGoodConfigPath(); processSnapshot != snapshot {
		if _, err := os.Stat(processSnapshot); err == nil {
			t.Fatalf("the process home gained a snapshot at %s", processSnapshot)
		}
	}
}

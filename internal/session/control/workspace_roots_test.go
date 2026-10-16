package control

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
)

func TestWorkspaceRootsAddListRemove(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", home)
	extraFolder := tempAside(t)
	primary, extra := testenv.TempDir(t), extraFolder()
	c := New(Options{WorkspaceRoot: primary})
	defer c.Close()

	got, err := c.AddWorkspaceRoot(extra)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.AddWorkspaceRoot(extra); err != nil {
		t.Fatalf("adding a granted root again must be idempotent: %v", err)
	}
	roots, err := c.WorkspaceRoots()
	if err != nil || roots.Primary != primary || !slices.Equal(roots.Extra, []string{got}) {
		t.Fatalf("roots = %+v, %v", roots, err)
	}
	if err := c.RemoveWorkspaceRoot(extra); err != nil {
		t.Fatal(err)
	}
	if roots, _ = c.WorkspaceRoots(); len(roots.Extra) != 0 {
		t.Fatalf("root survived removal: %+v", roots)
	}
	if err := c.RemoveWorkspaceRoot(extra); err != nil {
		t.Fatalf("removing a withdrawn root must succeed: %v", err)
	}
}

func TestWorkspaceRootsRefuseWhatCannotBeARoot(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", home)
	primary := testenv.TempDir(t)
	inside := filepath.Join(primary, "sub")
	file := filepath.Join(testenv.TempDir(t), "f.txt")
	for _, err := range []error{os.Mkdir(inside, 0o755), os.WriteFile(file, nil, 0o644)} {
		if err != nil {
			t.Fatal(err)
		}
	}
	state := filepath.Join(home, "sessions")
	if err := os.MkdirAll(state, 0o755); err != nil {
		t.Fatal(err)
	}
	userHome := testenv.TempDir(t)
	t.Setenv("HOME", userHome)
	t.Setenv("USERPROFILE", userHome)
	if err := os.Mkdir(filepath.Join(userHome, ".ssh"), 0o700); err != nil {
		t.Fatal(err)
	}
	c := New(Options{WorkspaceRoot: primary})
	defer c.Close()

	for name, tc := range map[string]struct {
		dir  string
		want WorkspaceRootRefusal
	}{
		"relative":        {"sub", RootNotDirectory},
		"missing":         {filepath.Join(primary, "nope"), RootNotDirectory},
		"file":            {file, RootNotDirectory},
		"inside primary":  {inside, RootCoveredAlready},
		"primary itself":  {primary, RootCoveredAlready},
		"user home":       {userHome, RootTooBroad},
		"filesystem root": {filepath.VolumeName(os.TempDir()) + string(filepath.Separator), RootTooBroad},
		"ssh dir":         {filepath.Join(userHome, ".ssh"), RootProtected},
		"state root":      {home, RootProtected},
		"under state":     {state, RootProtected},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := c.AddWorkspaceRoot(tc.dir)
			var refusal *WorkspaceRootError
			if !errors.As(err, &refusal) || refusal.Code != tc.want {
				t.Fatalf("AddWorkspaceRoot(%q) = %v, want %s", tc.dir, err, tc.want)
			}
		})
	}
	if roots, _ := c.WorkspaceRoots(); len(roots.Extra) != 0 {
		t.Fatalf("a refused folder was recorded: %+v", roots)
	}
}

// An isolated workspace is a worktree under Reasonix's own storage. It is not a
// folder anyone should trust wholesale: what it holds decides what the agent may
// do, and its tree is Reasonix's. The judgement is the same on every platform.
func TestTrustableFolderRefusesReasonixStorageOnEveryPlatform(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", home)
	userHome := testenv.TempDir(t)
	t.Setenv("HOME", userHome)
	t.Setenv("USERPROFILE", userHome)
	for _, id := range config.RootIDs() {
		dir := config.RootDir(id)
		if dir == "" {
			continue
		}
		if err := os.MkdirAll(filepath.Join(dir, "x"), 0o755); err != nil {
			t.Fatal(err)
		}
		if TrustableFolder(dir) || TrustableFolder(filepath.Join(dir, "x")) {
			t.Errorf("the %s root, or a folder inside it, was offered for trust", id)
		}
	}
	ordinary := tempAside(t)()
	if !TrustableFolder(ordinary) {
		t.Fatalf("an ordinary folder was refused")
	}
	if TrustableFolder(userHome) || TrustableFolder(filepath.Join(userHome, ".ssh")) {
		t.Fatal("home or its ssh directory was offered for trust")
	}
}

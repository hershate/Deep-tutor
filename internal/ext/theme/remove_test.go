package theme

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"reasonix/internal/base/testenv"
)

func TestRemoveDeletesAnInstalledPackAndNothingElse(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	writePack(t, Dir(), "dusk", minimal)
	writePack(t, Dir(), "keep", minimal)
	if err := Remove("dusk"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(Dir(), "dusk")); !os.IsNotExist(err) {
		t.Fatalf("pack directory survived: %v", err)
	}
	if _, err := os.Stat(filepath.Join(Dir(), "keep", manifestName)); err != nil {
		t.Fatalf("a neighbour was touched: %v", err)
	}
}

func TestRemoveRefusesWhatTheLibraryDoesNotHold(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	for _, id := range []string{"never-installed", "plugin:foo:bar"} {
		if err := Remove(id); !errors.Is(err, ErrNotInstalled) {
			t.Fatalf("Remove(%q) = %v, want ErrNotInstalled", id, err)
		}
	}
	for _, id := range []string{"", ".", "..", "a/b", "a\\b", "a\x00b", "dusk."} {
		if err := Remove(id); !errors.Is(err, ErrBadID) {
			t.Fatalf("Remove(%q) = %v, want ErrBadID", id, err)
		}
	}
}

func TestListMarksOnlyLibraryPacksRemovable(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	writePack(t, Dir(), "dusk", minimal)
	var mine, shipped int
	for _, p := range List() {
		switch {
		case p.ID == "dusk" && p.Removable:
			mine++
		case p.Removable:
			t.Fatalf("%q is marked removable but is not in the library", p.ID)
		default:
			shipped++
		}
	}
	if mine != 1 || shipped == 0 {
		t.Fatalf("mine=%d shipped=%d", mine, shipped)
	}
}

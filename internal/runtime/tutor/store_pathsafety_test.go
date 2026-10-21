package tutor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A name that carries a path reference must never be able to name a file
// outside the directory it belongs to. Go's ServeMux hands the store a
// decoded path segment, so "../../plans/index" is what a request can deliver.
func TestSafeSegmentKeepsNamesInside(t *testing.T) {
	for _, name := range []string{
		"../../plans/index",
		`..\..\plans\index`,
		"..",
		".",
		"",
		"a/b",
		`a\b`,
		"C:evil",
	} {
		got := safeSegment(name)
		if got == name && name != "" {
			t.Fatalf("safeSegment(%q) kept the name unchanged", name)
		}
		if strings.ContainsAny(got, `/\`) || strings.Contains(got, "..") || strings.Contains(got, ":") {
			t.Fatalf("safeSegment(%q) = %q still carries a path reference", name, got)
		}
	}
	if safeSegment("0123456789abcdef01234567") != "0123456789abcdef01234567" {
		t.Fatal("safeSegment must leave a normal id untouched")
	}
}

// The store's own path builders must not escape the store root, whatever the
// identifier carries.
func TestStorePathBuildersStayInsideRoot(t *testing.T) {
	store := openTestStore(t)
	root := store.Root()
	for _, id := range []string{"../../evil", `..\..\evil`, "/etc/passwd", ".."} {
		for _, p := range []string{
			store.sessionDir(id),
			store.planDir(id),
			store.notePath(id),
			store.kbDocPath(id),
			store.kbParsedPath(id),
			store.sharePath(id),
		} {
			rel, err := filepath.Rel(root, p)
			if err != nil {
				t.Fatalf("rel %s: %v", p, err)
			}
			if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
				t.Fatalf("path %q escaped the store root %q", p, root)
			}
		}
	}
}

// A delete that names a file outside its directory must not reach it.
func TestStoreDeleteNoteStaysInsideNotesDir(t *testing.T) {
	root := t.TempDir()
	store, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(root, "plans", "index.json")
	if err := os.MkdirAll(filepath.Dir(outside), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(outside, []byte(`{"plans":[]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := store.DeleteNote("../../plans/index"); err == nil {
		t.Fatal("delete of a missing note should report not found")
	}
	if _, err := os.Stat(outside); err != nil {
		t.Fatalf("the file outside the notes dir was removed: %v", err)
	}
}

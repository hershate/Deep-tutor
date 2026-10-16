package tutor

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeDraft(t *testing.T, work, name, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(filepath.Join(work, name)), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(work, name), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func runPromote(t *testing.T, tl promoteTool, args string) (string, error) {
	t.Helper()
	return tl.Execute(context.Background(), json.RawMessage(args))
}

func promoteFor(root, work string) promoteTool {
	return NewPromoteArtifactTool(root, work).(promoteTool)
}

func TestPromoteArtifactNote(t *testing.T) {
	root, work := t.TempDir(), t.TempDir()
	writeDraft(t, work, "plan.md", "# Study plan\n\nDaily drills.")
	out, err := runPromote(t, promoteFor(root, work), `{"name":"plan.md","target_kind":"note"}`)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "tutor note") {
		t.Fatalf("output should name the note: %q", out)
	}
	store, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	notes, err := store.Notes()
	if err != nil {
		t.Fatal(err)
	}
	if len(notes) != 1 {
		t.Fatalf("want 1 note, got %d", len(notes))
	}
	if notes[0].Title != "plan.md" || notes[0].Body != "# Study plan\n\nDaily drills." {
		t.Fatalf("note mismatch: %+v", notes[0])
	}
	if notes[0].ID == "" || notes[0].CreatedAt.IsZero() {
		t.Fatalf("store should stamp id and created_at: %+v", notes[0])
	}
}

func TestPromoteArtifactDefaultTargetIsNote(t *testing.T) {
	root, work := t.TempDir(), t.TempDir()
	writeDraft(t, work, "a.txt", "hello")
	if _, err := runPromote(t, promoteFor(root, work), `{"name":"a.txt"}`); err != nil {
		t.Fatal(err)
	}
	store, _ := OpenStore(root)
	notes, err := store.Notes()
	if err != nil {
		t.Fatal(err)
	}
	if len(notes) != 1 {
		t.Fatalf("default target should be note, got %d notes", len(notes))
	}
	if docs, err := store.KbDocs(); err != nil || len(docs) != 0 {
		t.Fatalf("default target should not touch the kb: %v %d", err, len(docs))
	}
}

func TestPromoteArtifactKBAndDedup(t *testing.T) {
	root, work := t.TempDir(), t.TempDir()
	writeDraft(t, work, "rust-notes.md", "# Rust\nOwnership rules worth keeping.")
	tl := promoteFor(root, work)
	out, err := runPromote(t, tl, `{"name":"rust-notes.md","target_kind":"kb"}`)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "knowledge base document") {
		t.Fatalf("output should name the document: %q", out)
	}
	// Same content under a different file name dedups rather than doubling.
	writeDraft(t, work, "copy.md", "# Rust\nOwnership rules worth keeping.")
	out2, err := runPromote(t, tl, `{"name":"copy.md","target_kind":"kb"}`)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out2, "already holds") {
		t.Fatalf("second promote should report the existing document: %q", out2)
	}
	store, _ := OpenStore(root)
	docs, err := store.KbDocs()
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != 1 {
		t.Fatalf("want 1 kb doc after dedup, got %d", len(docs))
	}
	if docs[0].Status != "parsed" || docs[0].Title != "rust-notes.md" {
		t.Fatalf("doc mismatch: %+v", docs[0])
	}
	if notes, _ := store.Notes(); len(notes) != 0 {
		t.Fatalf("kb target should not create notes, got %d", len(notes))
	}
}

func TestPromoteArtifactGuards(t *testing.T) {
	root, work := t.TempDir(), t.TempDir()
	writeDraft(t, work, "keep.md", "content")
	writeDraft(t, work, "empty.md", "   ")
	os.MkdirAll(filepath.Join(work, "sub"), 0o755)
	cases := []struct {
		name string
		args string
		want string
	}{
		{name: "missing name", args: `{}`, want: "name is required"},
		{name: "blank name", args: `{"name":"   "}`, want: "name is required"},
		// A rooted unix path is refused as absolute on unix hosts; on Windows
		// Go joins it under the workspace instead, so the containment holds
		// either way and the read fails inside it.
		{name: "rooted unix path", args: `{"name":"/etc/passwd"}`, want: ""},
		{name: "absolute windows path", args: `{"name":"C:\\Windows\\evil.txt"}`, want: "inside the workspace"},
		{name: "parent escape", args: `{"name":"../escape.md"}`, want: "inside the workspace"},
		{name: "nested escape", args: `{"name":"a/../../escape.md"}`, want: "inside the workspace"},
		{name: "workspace itself", args: `{"name":"."}`, want: "inside the workspace"},
		{name: "missing file", args: `{"name":"nope.md"}`, want: "read draft"},
		{name: "empty draft", args: `{"name":"empty.md"}`, want: "draft is empty"},
		{name: "bad target", args: `{"name":"keep.md","target_kind":"material"}`, want: "target_kind"},
		{name: "dt plan target", args: `{"name":"keep.md","target_kind":"plan"}`, want: "target_kind"},
	}
	tl := promoteFor(root, work)
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			out, err := runPromote(t, tl, tc.args)
			if err == nil {
				t.Fatalf("want an error, got output %q", out)
			}
			if tc.want != "" && !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("want error containing %q, got %v", tc.want, err)
			}
		})
	}
}

func TestPromoteDraftPathAllowsNestedNames(t *testing.T) {
	work := t.TempDir()
	full, err := promoteDraftPath(work, "drafts/plan.md")
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(work, "drafts", "plan.md"); full != want {
		t.Fatalf("want %q, got %q", want, full)
	}
	// A name that resolves back inside the workspace is fine.
	if _, err := promoteDraftPath(work, "drafts/../plan.md"); err != nil {
		t.Fatalf("in-workspace normalization should pass: %v", err)
	}
	if _, err := promoteDraftPath("", "plan.md"); err == nil {
		t.Fatal("no workspace should be refused")
	}
}

func TestPromoteArtifactToolContract(t *testing.T) {
	tl := NewPromoteArtifactTool(t.TempDir(), t.TempDir())
	if tl.Name() != "promote_artifact" {
		t.Fatalf("name: %q", tl.Name())
	}
	if tl.ReadOnly() {
		t.Fatal("promote writes to the tutor library, so it is a writer")
	}
	var schema map[string]any
	if err := json.Unmarshal(tl.Schema(), &schema); err != nil {
		t.Fatalf("schema should be valid json: %v", err)
	}
}

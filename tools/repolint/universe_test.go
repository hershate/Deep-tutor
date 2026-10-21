package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"testing"

	"deep-tutor/internal/base/testenv"
)

// A local copy of the source is not this repository. The linter measured REF/
// as its own debt, so the baseline carried budgets for thousands of files the
// repository does not contain: the same tree measured on a clean checkout
// reported fewer findings than the baseline recorded, and every run on a
// machine that happened to keep the copy measured a different repository.
func TestIgnoredTreesAreNotCollected(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not on PATH")
	}
	root := testenv.TempDir(t)
	write := func(rel, body string) {
		t.Helper()
		p := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	git := func(args ...string) {
		t.Helper()
		cmd := exec.Command("git", args...)
		cmd.Dir = root
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
	write(".gitignore", "REF/\n")
	write("internal/tracked.go", "package internal\n")
	write("internal/testdata/fixture.go", "package testdata\n")
	write(".hidden/hidden.go", "package hidden\n")
	write("REF/extracted/copy.go", "package extracted\n")
	write("internal/new.go", "package internal\n")

	git("init", "-q")
	git("add", ".gitignore", "internal/tracked.go", "internal/testdata/fixture.go", ".hidden/hidden.go")

	got, err := collect(root)
	if err != nil {
		t.Fatalf("collect: %v", err)
	}
	for _, want := range []string{"internal/new.go", "internal/tracked.go"} {
		if !slices.Contains(got, want) {
			t.Errorf("%s was not collected: %v", want, got)
		}
	}
	for _, unwanted := range []string{
		"REF/extracted/copy.go",
		"internal/testdata/fixture.go",
		".hidden/hidden.go",
	} {
		if slices.Contains(got, unwanted) {
			t.Errorf("%s was collected: %v", unwanted, got)
		}
	}
}

// A leftover file inside an ignored tree is a local artefact. Reporting it
// sends the reader to delete a file the repository never had.
func TestStrayFilesInsideIgnoredTreesAreNotReported(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not on PATH")
	}
	root := testenv.TempDir(t)
	write := func(rel, body string) {
		t.Helper()
		p := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(".gitignore", "REF/\n")
	write("internal/kept.go", "package internal\n")
	write("internal/kept.go.orig", "leftover\n")
	write("REF/extracted/copy.go.orig", "leftover\n")

	cmd := exec.Command("git", "init", "-q")
	cmd.Dir = root
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git init: %v\n%s", err, out)
	}
	cmd = exec.Command("git", "add", ".gitignore", "internal/kept.go")
	cmd.Dir = root
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git add: %v\n%s", err, out)
	}

	findings := checkStrayFiles(root)
	for _, f := range findings {
		if filepath.ToSlash(f.File) == "REF/extracted/copy.go.orig" {
			t.Errorf("a leftover inside an ignored tree was reported: %v", findings)
		}
	}
	found := false
	for _, f := range findings {
		if filepath.ToSlash(f.File) == "internal/kept.go.orig" {
			found = true
		}
	}
	if !found {
		t.Errorf("a leftover in the repository was not reported: %v", findings)
	}
}

// universe.go — the tree this linter reads.
//
// A finding here is a statement about this repository: it enters the
// baseline, and it names debt somebody in this repository can pay. A walk
// reads more than that — a local source copy, a build tree, a package cache,
// trees nobody commits and nobody owns — and reports their debt as ours.
// Worse, a baseline built from that universe does not reproduce on a clean
// checkout: without the copy the same tree reports fewer findings.
package main

import (
	"io/fs"
	"os/exec"
	"path/filepath"
	"strings"
)

// isWorkTree reports whether root sits inside a git work tree.
func isWorkTree(root string) bool {
	cmd := exec.Command("git", "rev-parse", "--git-dir")
	cmd.Dir = root
	return cmd.Run() == nil
}

// visibleFiles lists the files a reviewer would see in the tree: what git
// tracks, plus the new ones git does not ignore. Outside a work tree there
// is no index to ask, so the tree is walked.
func visibleFiles(root string) ([]string, error) {
	if !isWorkTree(root) {
		return walkFiles(root)
	}
	return repoFiles(root)
}

// walkFiles lists every file under root. It answers for a tree that is not a
// git work tree, where there is no index to ask.
func walkFiles(root string) ([]string, error) {
	var out []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if path == root {
				return nil
			}
			if skipDirs[d.Name()] || strings.HasPrefix(d.Name(), ".") {
				return filepath.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		out = append(out, filepath.ToSlash(rel))
		return nil
	})
	return out, err
}

// ignoredPrefixes lists the paths git ignores, relative to root. A walk that
// descends into one reads files nobody committed.
func ignoredPrefixes(root string) []string {
	listed, err := gitPaths(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
	if err != nil {
		return nil
	}
	out := make([]string, 0, len(listed))
	for _, rel := range listed {
		rel = strings.TrimSpace(rel)
		if rel == "" {
			continue
		}
		out = append(out, strings.TrimSuffix(rel, "/"))
	}
	return out
}

// underIgnored reports whether a root-relative path lies inside a tree git
// ignores.
func underIgnored(rel string, ignored []string) bool {
	rel = filepath.ToSlash(rel)
	if rel == "" || rel == "." {
		return false
	}
	for _, p := range ignored {
		if rel == p || strings.HasPrefix(rel, p+"/") {
			return true
		}
	}
	return false
}

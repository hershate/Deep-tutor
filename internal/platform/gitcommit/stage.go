package gitcommit

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"reasonix/internal/platform/gitcmd"
	"reasonix/internal/platform/gitstatus"
)

// ErrUnknownPath is a path that git does not report as changed in the
// workspace. Only what git itself lists can be staged, so a traversal, an
// option spelling or a name that is not a change never reaches a command.
var ErrUnknownPath = errors.New("gitcommit: not a changed file in the workspace")

// Stage records the named changes in the index, or every listed change when all
// is set. Deletions are staged too.
func Stage(ctx context.Context, repo gitcmd.Repo, paths []string, all bool) error {
	return alter(ctx, repo, paths, all, func(c gitstatus.Change) []string {
		if all && !c.Unstaged {
			return nil
		}
		return []string{c.Path}
	}, func(ctx context.Context, repo gitcmd.Repo) []string {
		return []string{"add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul"}
	})
}

// Unstage returns the named changes to the work tree, which keeps their
// content, or every staged change when all is set. A rename gives back both of
// its halves.
func Unstage(ctx context.Context, repo gitcmd.Repo, paths []string, all bool) error {
	return alter(ctx, repo, paths, all, func(c gitstatus.Change) []string {
		if (all && !c.Staged) || (!all && !c.Staged) {
			return nil
		}
		if c.OldPath != "" {
			return []string{c.Path, c.OldPath}
		}
		return []string{c.Path}
	}, func(ctx context.Context, repo gitcmd.Repo) []string {
		if repo.Command(ctx, "rev-parse", "--verify", "--quiet", "HEAD").Run() == nil {
			return []string{"restore", "--staged", "--pathspec-from-file=-", "--pathspec-file-nul"}
		}
		// Before the first commit there is no HEAD to restore from: the index
		// is simply emptied of the path.
		return []string{"rm", "--cached", "-r", "-q", "--ignore-unmatch", "--pathspec-from-file=-", "--pathspec-file-nul"}
	})
}

func alter(ctx context.Context, repo gitcmd.Repo, paths []string, all bool,
	specs func(gitstatus.Change) []string, command func(context.Context, gitcmd.Repo) []string) error {
	changes, ok, err := gitstatus.Status(ctx, repo)
	if err != nil {
		return fmt.Errorf("git status: %w", err)
	}
	if !ok {
		return ErrNoRepository
	}
	listed := make(map[string]gitstatus.Change, len(changes))
	for _, c := range changes {
		listed[c.Path] = c
	}
	var chosen []gitstatus.Change
	if all {
		for _, c := range changes {
			if !isDirectory(repo.Dir, c.Path) {
				chosen = append(chosen, c)
			}
		}
	} else {
		if len(paths) == 0 {
			return ErrUnknownPath
		}
		for _, p := range paths {
			c, ok := listed[p]
			if !ok || isDirectory(repo.Dir, p) {
				return fmt.Errorf("%w: %q", ErrUnknownPath, p)
			}
			chosen = append(chosen, c)
		}
	}
	var input bytes.Buffer
	n := 0
	for _, c := range chosen {
		for _, p := range specs(c) {
			input.WriteString(":(literal)" + p + "\x00")
			n++
		}
	}
	if n == 0 {
		return nil
	}
	args := command(ctx, repo)
	cmd := repo.Command(ctx, args...)
	cmd.Stdin = &input
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("git %s: %w: %s", args[0], err, strings.TrimSpace(stderr.String()))
	}
	return nil
}

// isDirectory is true for a nested repository or a submodule, the only
// directories status lists. git would run inside one, under its own
// configuration, to stage it, so such a path is never handed to a command.
func isDirectory(root, rel string) bool {
	st, err := os.Lstat(filepath.Join(root, filepath.FromSlash(rel)))
	return err == nil && st.IsDir()
}

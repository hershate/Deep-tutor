package control

import (
	"fmt"
	"path/filepath"
	"slices"
	"strings"

	"reasonix/internal/contract/config"
)

// WorkspaceRoots is the folder this session belongs to and the extra folders
// the user granted it write access to. Extra roots contribute files only:
// configuration, memory, skills and sessions stay keyed by the primary root.
type WorkspaceRoots struct {
	Primary string
	Extra   []string
}

// WorkspaceRootRefusal and its codes are config's: the same vetting runs when a
// grant is stored and every time one is loaded.
type WorkspaceRootRefusal = config.WorkspaceRootRefusal

const (
	RootNotDirectory   = config.RootNotDirectory
	RootCoveredAlready = config.RootCoveredAlready
	RootTooBroad       = config.RootTooBroad
	RootProtected      = config.RootProtected
)

// WorkspaceRootError carries the refusal as an identity a frontend can map.
type WorkspaceRootError = config.WorkspaceRootError

// WorkspaceRoots reads the primary root and the granted extras.
func (c *Controller) WorkspaceRoots() (WorkspaceRoots, error) {
	out := WorkspaceRoots{Primary: c.WorkspaceRoot()}
	if out.Primary == "" {
		return out, nil
	}
	grant, err := config.NewProjectGrantStore(config.Roots{}.Home()).Grant(out.Primary)
	if err != nil {
		return out, err
	}
	for _, dir := range grant.AllowWrite {
		if filepath.IsAbs(dir) {
			out.Extra = append(out.Extra, dir)
		}
	}
	return out, nil
}

// AddWorkspaceRoot grants this workspace write access to dir and returns the
// resolved path it recorded. The grant lives in the user's home, so a checkout
// cannot add roots to itself. The caller rebuilds the runtime to apply it.
func (c *Controller) AddWorkspaceRoot(dir string) (string, error) {
	primary := c.WorkspaceRoot()
	if primary == "" {
		return "", fmt.Errorf("%w: no workspace is open", config.ErrProjectGrantsUnavailable)
	}
	cfg, err := config.LoadForRootReadOnly(primary)
	if err != nil {
		return "", err
	}
	resolved, err := cfg.VetExtraRoot(primary, dir, cfg.ExtraRoots())
	if err != nil {
		return "", err
	}
	err = config.NewProjectGrantStore(config.Roots{}.Home()).Update(primary, func(g config.ProjectGrant) (config.ProjectGrant, error) {
		if !slices.Contains(g.AllowWrite, resolved) {
			g.AllowWrite = append(g.AllowWrite, resolved)
		}
		return g, nil
	})
	return resolved, err
}

// RemoveWorkspaceRoot withdraws a granted extra root. An entry that is not
// there is already withdrawn.
func (c *Controller) RemoveWorkspaceRoot(dir string) error {
	primary := c.WorkspaceRoot()
	if primary == "" || strings.TrimSpace(dir) == "" {
		return fmt.Errorf("%w: workspace and folder are required", config.ErrProjectGrantsUnavailable)
	}
	resolved := resolvedOrSelf(dir)
	return config.NewProjectGrantStore(config.Roots{}.Home()).Update(primary, func(g config.ProjectGrant) (config.ProjectGrant, error) {
		g.AllowWrite = slices.DeleteFunc(g.AllowWrite, func(saved string) bool { return samePath(saved, dir) || samePath(saved, resolved) })
		return g, nil
	})
}

func resolvedOrSelf(p string) string {
	if real, err := filepath.EvalSymlinks(p); err == nil {
		return real
	}
	return filepath.Clean(p)
}

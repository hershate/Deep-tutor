package control

import "deep-tutor/internal/contract/config"

// runtimeConfig loads the configuration a running session reads: the user-global
// layer from the home the assembly stated, and the project layer from the
// session's own workspace. A server hosting several projects must not hand every
// session the configuration of the folder it happened to start in, so the project
// root is the workspace, never the process working directory.
func runtimeConfig(roots config.Roots, root string) (*config.Config, error) {
	// Read-only: these are query paths, and a query must not rewrite the
	// user's configuration.
	return roots.LoadForRootReadOnly(root)
}

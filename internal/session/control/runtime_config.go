package control

import "deep-tutor/internal/contract/config"

// runtimeConfig loads the configuration a running session reads: the user-global
// layer from the home the assembly stated, and the project layer from the
// process working directory exactly as before.
//
// The project root is deliberately the process working directory and not the
// session's workspace. A session reading another project's reasonix.toml is a
// real defect, but changing which file wins is a separate decision from
// changing which home wins, and mixing the two would make this change hard to
// review. The binding this function does honour is the one that matters for
// isolation: two users in one process must not read each other's settings.
//
// The zero Roots follows the environment, which is what a single-user host
// wants and what every caller that states no home gets.
func runtimeConfig(roots config.Roots) (*config.Config, error) {
	return roots.LoadForRoot(".")
}

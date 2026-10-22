package boot

import (
	"deep-tutor/internal/contract/config"
)

// effectiveSecrets is the credential-protection state this assembly enforces.
// The layers are process-wide — the tool layer reads them from one place — so a
// build serving one user of many takes them from the global configuration; a
// user's own file must not decide them for the others.
func effectiveSecrets(opts Options, roots config.Roots, loaded config.SecretsConfig) config.SecretsConfig {
	if opts.GlobalSecrets != nil {
		return *opts.GlobalSecrets
	}
	if roots.StatedHome() == "" {
		return loaded
	}
	global, err := config.ProcessSecrets()
	if err != nil {
		// A global configuration that cannot be read must not lower the
		// protection: the built-in default keeps credential files protected.
		return config.DefaultSecretsConfig()
	}
	return global
}

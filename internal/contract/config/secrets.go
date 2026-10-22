package config

import (
	"os"

	fileencoding "deep-tutor/internal/base/fileutil/encoding"
)

// DefaultSecretsConfig is what an absent [secrets] section means: credential
// files stay protected, the two opt-in layers stay off. A protection that
// cannot be read must not fall to the zero value, which would turn it off.
func DefaultSecretsConfig() SecretsConfig {
	return SecretsConfig{ProtectCredentialFiles: true}
}

// Secrets returns the credential-protection settings this binding's user
// configuration declares. It reads the user file alone: no project layer, no
// credential store, no environment expansion.
func (r Roots) Secrets() (SecretsConfig, error) {
	cfg := r.defaultConfig()
	path := r.userConfigLoadPath()
	if path == "" {
		return cfg.Secrets, nil
	}
	data, err := fileencoding.ReadFileUTF8(path)
	if err != nil {
		if os.IsNotExist(err) {
			return cfg.Secrets, nil
		}
		return cfg.Secrets, err
	}
	if _, err := decodeTOMLBytes(data, cfg); err != nil {
		return cfg.Secrets, err
	}
	return cfg.Secrets, nil
}

// ProcessSecrets returns the credential-protection settings the process's own
// configuration declares. A process serving several users enforces one set,
// and that set is the one the process itself is configured with.
func ProcessSecrets() (SecretsConfig, error) { return processRoots().Secrets() }

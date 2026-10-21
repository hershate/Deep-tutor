package envvar

import (
	"os"
	"strings"
)

const (
	// CurrentPrefix is the prefix of the renamed variables.
	CurrentPrefix = "DEEPTUTOR_"
	// LegacyPrefix is the prefix they carried before the rename.
	LegacyPrefix = "REASONIX_"
)

// Get returns the value of the named variable. A name this product renamed
// resolves to the current spelling first and the legacy one second; every
// other name is read as it stands.
func Get(name string) string {
	legacy, renamed := legacyName(name)
	if !renamed {
		return os.Getenv(name)
	}
	if v := os.Getenv(name); v != "" {
		return v
	}
	return os.Getenv(legacy)
}

// Lookup is Get with the "was it set" answer, for a caller that has to tell
// an unset variable from one that is set to nothing.
func Lookup(name string) (string, bool) {
	legacy, renamed := legacyName(name)
	if !renamed {
		return os.LookupEnv(name)
	}
	if v, ok := os.LookupEnv(name); ok && v != "" {
		return v, true
	}
	if v, ok := os.LookupEnv(legacy); ok {
		return v, true
	}
	return "", false
}

// Name is the spelling of the named variable that is actually in effect: the
// current one when it is set, the legacy one when only that is. A surface that
// tells a user which variable pins a location has to name the one that does —
// naming the other leaves them changing a variable nothing reads.
func Name(name string) string {
	legacy, renamed := legacyName(name)
	if !renamed {
		return name
	}
	if os.Getenv(name) != "" {
		return name
	}
	if os.Getenv(legacy) != "" {
		return legacy
	}
	return name
}

// CurrentName is the current spelling of a variable that carries the
// product's name. A contract keyed by the legacy name — the credential
// store's own keys are, because renaming a key orphans what is stored under
// it — uses it to find the variable that overrides it.
func CurrentName(legacy string) string {
	if !strings.HasPrefix(legacy, LegacyPrefix) {
		return legacy
	}
	return CurrentPrefix + strings.TrimPrefix(legacy, LegacyPrefix)
}

// legacyName is the old spelling of a renamed variable, and whether the name
// is one this product renamed at all.
func legacyName(name string) (string, bool) {
	if !strings.HasPrefix(name, CurrentPrefix) {
		return "", false
	}
	return LegacyPrefix + strings.TrimPrefix(name, CurrentPrefix), true
}

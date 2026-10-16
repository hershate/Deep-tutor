package config

import "strings"

// HostVerificationOff reports whether the user turned off what the host asks of
// a turn that changed files: a check, a review and a sign-off before it may
// finish. Any other value, including a misspelt one, leaves them on.
func (c *Config) HostVerificationOff() bool {
	return c != nil && strings.EqualFold(strings.TrimSpace(c.Agent.HostVerification), "off")
}

package config

import "strings"

// WriteLeaseOptimistic reports whether this session's cross-session write lease
// leaves a writer that declared no extent out of the whole-workspace hold. Any
// other value, including a misspelt one, is the strict default.
func (c *Config) WriteLeaseOptimistic() bool {
	return c != nil && strings.EqualFold(strings.TrimSpace(c.Agent.WriteLease), "optimistic")
}

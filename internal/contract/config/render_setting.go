package config

import (
	"fmt"
	"strings"
)

// agentSetting writes one [agent] key as an active line when it differs from its
// default, and as a commented example otherwise, so a later change of default
// still reaches files written today.
func agentSetting[T int | string](b *strings.Builder, key string, value, def T, note string) {
	if value != def {
		fmt.Fprintf(b, "%s = %v   # %s\n", key, quoteIfString(value), note)
		return
	}
	fmt.Fprintf(b, "# %s = %v   # %s\n", key, quoteIfString(def), note)
}

func quoteIfString[T int | string](v T) any {
	if s, ok := any(v).(string); ok {
		return fmt.Sprintf("%q", s)
	}
	return v
}

package config

import (
	"errors"
	"fmt"
	"maps"
	"slices"
	"strings"
)

var ErrModelAliasUnlisted = errors.New("model alias does not name a selected model")

// ModelAlias is display metadata for one exact model ID, never a lookup alias.
func (e *ProviderEntry) ModelAlias(model string) string {
	return strings.TrimSpace(e.ModelAliases[model])
}

// SetModelAliases replaces aliases for listed models while retaining unlisted
// models' metadata. Omitted or blank values clear a listed model's alias.
func (e *ProviderEntry) SetModelAliases(aliases map[string]string) error {
	models := e.ModelList()
	next := maps.Clone(e.ModelAliases)
	if next == nil {
		next = map[string]string{}
	}
	for model, value := range aliases {
		if !slices.Contains(models, model) {
			return fmt.Errorf("%q: %w", model, ErrModelAliasUnlisted)
		}
		label, err := NormalizeProviderDisplayName(value)
		if err != nil {
			return err
		}
		next[model] = label
	}
	for _, model := range models {
		if strings.TrimSpace(aliases[model]) == "" {
			delete(next, model)
		}
	}
	if len(next) == 0 {
		next = nil
	}
	e.ModelAliases = next
	return nil
}

func renderModelAliases(b *strings.Builder, aliases map[string]string) {
	if len(aliases) > 0 {
		fmt.Fprintf(b, "model_aliases = %s\n", renderStringMap(aliases))
	}
}

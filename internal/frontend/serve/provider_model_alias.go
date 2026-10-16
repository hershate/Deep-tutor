package serve

import (
	"errors"

	"reasonix/internal/contract/config"
)

func applyModelAliases(entry *config.ProviderEntry, sent *map[string]string) *editRefusal {
	if sent == nil {
		return nil
	}
	err := entry.SetModelAliases(*sent)
	switch {
	case errors.Is(err, config.ErrModelAliasUnlisted):
		return &editRefusal{"provider.model_alias_unlisted", "model aliases must name selected models by their exact IDs", nil}
	case errors.Is(err, config.ErrProviderDisplayNameTooLong):
		return &editRefusal{"provider.display_name_too_long", "the display name is too long", map[string]any{"max": config.MaxProviderDisplayNameRunes}}
	case errors.Is(err, config.ErrProviderDisplayNameInvalid):
		return &editRefusal{"provider.display_name_invalid", "the display name contains a control character", nil}
	}
	return nil
}

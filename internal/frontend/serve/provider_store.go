package serve

import (
	"errors"
	"net/http"
	"strings"

	"reasonix/internal/contract/config"
)

// storeProviderEntries is the one way a new source reaches the config file:
// pick each entry's key slot (reusing one on the same host), write the key once
// per slot, upsert and save. A refusal is written to w and reported as false.
// Adding by hand and creating from a preset both end here, so the same key
// reuse, bad-slot and write-failure answers come from one place.
func storeProviderEntries(w http.ResponseWriter, known *config.Config, entries []config.ProviderEntry, apiKey string, replace bool) bool {
	key := strings.TrimSpace(apiKey)
	stored := map[string]bool{}
	cfg := config.LoadForEdit(config.UserConfigPath())
	for _, entry := range entries {
		var err error
		entry.APIKeyEnv, err = keyEnvForNewSource(known, entry.Name, entry.BaseURL, apiKey, replace)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err)
			return false
		}
		if key != "" && !stored[entry.APIKeyEnv] {
			if _, err := config.SetCredential(entry.APIKeyEnv, key); err != nil {
				// The slot is derived from the name, so this is the person's to
				// fix and the name is the field to point them at, not the key.
				if errors.Is(err, config.ErrInvalidCredentialKey) {
					refuse(w, http.StatusBadRequest, "provider.bad_key_slot",
						"the name does not make a usable credential slot",
						map[string]any{"name": entry.Name, "slot": entry.APIKeyEnv})
					return false
				}
				writeErr(w, http.StatusInternalServerError, err)
				return false
			}
			stored[entry.APIKeyEnv] = true
		}
		if err := cfg.UpsertProvider(entry); err != nil {
			writeErr(w, http.StatusBadRequest, err)
			return false
		}
	}
	if err := cfg.SaveTo(config.UserConfigPath()); err != nil {
		writeErr(w, http.StatusInternalServerError, err)
		return false
	}
	return true
}

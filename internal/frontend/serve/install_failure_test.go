package serve

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"reasonix/internal/ext/installsource"
)

func TestInstallFailureNamesTheCauseTheToolKnows(t *testing.T) {
	for want, err := range map[string]error{
		"install.source_unreadable":          installsource.ErrSourceUnreadable,
		"install.manifest_missing":           installsource.ErrManifestMissing,
		"install.manifest_invalid":           installsource.ErrInvalidManifest,
		"install.unsupported_kind":           installsource.ErrUnsupportedKind,
		"install.no_compatible_capabilities": installsource.ErrNoCompatibleCapabilities,
		"install.auth_required":              installsource.ErrAuthRequired,
		"install.binary_missing":             installsource.ErrBinaryMissing,
		"install.already_exists":             installsource.ErrAlreadyExists,
		"install.failed":                     fmt.Errorf("something else"),
	} {
		rec := httptest.NewRecorder()
		installFailure(rec, fmt.Errorf("install_source: wrapped: %w", err))
		var body struct {
			Code string `json:"code"`
		}
		_ = json.NewDecoder(rec.Body).Decode(&body)
		if body.Code != want || rec.Code == http.StatusOK {
			t.Errorf("%v: code %q status %d, want %q", err, body.Code, rec.Code, want)
		}
	}
}

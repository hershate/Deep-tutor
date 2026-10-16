package serve

import (
	"errors"
	"net/http"

	"reasonix/internal/ext/installsource"
)

// installFailure answers an install the tool refused with the cause the tool
// knows. A frontend that is handed only install.failed has to read the sentence
// to tell an unreadable source from a manifest that did not validate.
func installFailure(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, installsource.ErrSourceUnreadable):
		saveFailed(w, http.StatusUnprocessableEntity, "install.source_unreadable", err)
	case errors.Is(err, installsource.ErrManifestMissing):
		saveFailed(w, http.StatusUnprocessableEntity, "install.manifest_missing", err)
	case errors.Is(err, installsource.ErrInvalidManifest):
		saveFailed(w, http.StatusUnprocessableEntity, "install.manifest_invalid", err)
	case errors.Is(err, installsource.ErrUnsupportedKind):
		saveFailed(w, http.StatusUnprocessableEntity, "install.unsupported_kind", err)
	case errors.Is(err, installsource.ErrNoCompatibleCapabilities):
		saveFailed(w, http.StatusUnprocessableEntity, "install.no_compatible_capabilities", err)
	case errors.Is(err, installsource.ErrAuthRequired):
		saveFailed(w, http.StatusUnprocessableEntity, "install.auth_required", err)
	case errors.Is(err, installsource.ErrBinaryMissing):
		saveFailed(w, http.StatusUnprocessableEntity, "install.binary_missing", err)
	case errors.Is(err, installsource.ErrAlreadyExists):
		saveFailed(w, http.StatusConflict, "install.already_exists", err)
	default:
		saveFailed(w, http.StatusUnprocessableEntity, "install.failed", err)
	}
}

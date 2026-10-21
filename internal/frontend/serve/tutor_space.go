// tutor_space.go — the tutor surface's per-caller state space.
//
// One store root is one space: sessions, plans, notes, the knowledge base and
// the usage ledger all live under it. The kernel's auth gate has a single
// operator today, so the surface is a single-user local one.
//
// The space is the seam a multi-user front door plugs into: the plan puts
// the user concept in internal/web, so a request reads one root.
package serve

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"path/filepath"
	"strings"
	"sync"

	"deep-tutor/internal/runtime/tutor"
)

// tutorDefaultSpace is the space a request belongs to when no resolver is
// installed: the whole store, where it already is.
const tutorDefaultSpace = "default"

var (
	// tutorSpaceMu guards the space resolver and the registered services.
	tutorSpaceMu sync.RWMutex
	// tutorSpaceResolved names the space a request belongs to.
	tutorSpaceResolved func(*http.Request) string
	// tutorSpaceServices holds the services a host built itself, one per
	// space.
	tutorSpaceServices = map[string]*tutor.Service{}
)

// SetTutorSpaceResolver installs the resolver that names a request's state
// space. A host that authenticates users (internal/web/auth, stage ① M2/M3)
// sets it to the authenticated user's id, so the space's files are that user's
// alone. The key must come from an authenticated principal, never from a
// request header: a client-chosen key is a client-chosen directory.
func SetTutorSpaceResolver(fn func(*http.Request) string) {
	tutorSpaceMu.Lock()
	tutorSpaceResolved = fn
	tutorSpaceMu.Unlock()
}

// SetTutorSpaceService registers the tutor service for a state space. A
// host that assembles a per-user service itself (the plan's per-user
// assembly) registers it here; the serve layer then never builds one for
// that space.
func SetTutorSpaceService(space string, svc *tutor.Service) {
	tutorSpaceMu.Lock()
	if svc == nil {
		delete(tutorSpaceServices, space)
	} else {
		tutorSpaceServices[space] = svc
	}
	tutorSpaceMu.Unlock()
}

// tutorSpaceKey names the space a request belongs to.
func tutorSpaceKey(r *http.Request) string {
	tutorSpaceMu.RLock()
	fn := tutorSpaceResolved
	tutorSpaceMu.RUnlock()
	if fn != nil {
		if key := strings.TrimSpace(fn(r)); key != "" {
			return key
		}
	}
	return tutorDefaultSpace
}

// tutorSpaceRoot is the store root a space reads and writes. The default
// space keeps the store where it already is, so an existing installation
// keeps its data and nothing has to be migrated; every other space is a
// subtree of its own.
func tutorSpaceRoot(root, space string) string {
	if root == "" || space == "" || space == tutorDefaultSpace {
		return root
	}
	return filepath.Join(root, "spaces", tutorSpaceSegment(space))
}

// tutorSpaceSegment reduces a space key to a directory name that cannot
// climb out of the spaces directory. The reduction carries a digest of the
// original key, so two keys that reduce to the same characters still land
// in different directories.
func tutorSpaceSegment(space string) string {
	var b strings.Builder
	for _, r := range space {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == '-' || r == '_':
			b.WriteRune(r)
		default:
			b.WriteByte('_')
		}
	}
	stem := strings.Trim(b.String(), "._")
	if len(stem) > 48 {
		stem = stem[:48]
	}
	if stem == "" {
		stem = "space"
	}
	sum := sha256.Sum256([]byte(space))
	return stem + "-" + hex.EncodeToString(sum[:4])
}

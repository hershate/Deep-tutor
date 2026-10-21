package config

import (
	"os"

	"deep-tutor/internal/base/scratchroot"
)

// tempRoots lists the directories outside the workspace that the host may
// write to and that a workspace root may not overlap: the operating system's
// temporary directory and the project's scratch area.
//
// The scratch area is not the system temporary directory. It lives on the
// volume that holds the project, so a machine whose system volume is small
// keeps its regenerable output off it. Both are listed because the two are
// not the same place: the session-private temporary directories and the job
// scratch trees land in the scratch area, while the operating system's own
// temporary directory stays writable for everything else that writes there.
//
// A caller that redirects its temporary directory is followed there instead:
// the scratch area then is that directory, and listing both would name the
// same place twice.
func tempRoots() []string {
	system := os.TempDir()
	roots := []string{}
	if system != "" {
		roots = append(roots, system)
	}
	scratch := scratchroot.Base()
	if scratch != "" && !samePath(scratch, system) {
		roots = append(roots, scratch)
	}
	return roots
}

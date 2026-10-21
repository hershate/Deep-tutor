// Package scratchroot resolves the directory the project keeps disposable
// scratch state in.
//
// Scratch state is disposable by definition: test temporaries, the
// session-private temporary directories and the runtime scratch trees all
// hold content that can be regenerated and that no user ever reads. What
// matters is where it lands.
//
// The system volume is the wrong place for it. It is small, the operating
// system reclaims it on its own schedule, and it holds every other tenant of
// the machine. A project that writes trees rather than files needs a volume
// that is sized for that.
//
// Resolution order:
//
//  1. The temporary directory the operating system reports, when it is one
//     the caller redirected. A harness that isolates through TMPDIR is
//     stating where its temporaries go, and nothing overrides that.
//  2. $DEEPTUTOR_SCRATCH_DIR, when it holds a path. This is the operator's
//     explicit choice for a deployment.
//  3. TEMP on the volume that holds the project, when that volume is not the
//     one the operating system keeps its own temporary files on. TEMP is the
//     conventional name for a scratch area, so the layout stays
//     recognizable; the project's own subdirectory keeps the area to itself.
//  4. The operating system's temporary directory.
//
// Base answers with a directory; callers create their own children under it.
package scratchroot

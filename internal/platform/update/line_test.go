package update

import "testing"

// Studio's installer only keeps a pinned taskbar icon and its shortcuts when it
// is told it is replacing an install; the update path is the one that knows.
func TestStudioInstallerIsStartedAsAnUpdate(t *testing.T) {
	if got := StudioLine().Windows.InstallerArgs; got != "--updated" {
		t.Fatalf("Studio's update starts its installer with %q, want --updated", got)
	}
}

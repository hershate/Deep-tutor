package bootstrap

import (
	"strings"
	"testing"
)

// The remote kernel's file name is not a label: it is what the kernel was
// uploaded as, and what the running process's command line carries. The
// bootstrap finds the kernel by that name and decides whether a running serve
// is its own by matching it. A name the code does not know is a kernel it
// cannot find, cannot stop and cannot reuse.
//
// The names are two because both exist in the world: the remote is
// provisioned from the upstream release channel, whose archives ship a
// "reasonix" binary, and a host that already runs this build's own package
// carries "deep-tutor".
func TestRemoteKernelNamesCoverBothSpellings(t *testing.T) {
	want := map[string]bool{"reasonix": false, "deep-tutor": false}
	for _, n := range remoteKernelNames {
		if _, ok := want[n]; !ok {
			t.Fatalf("unexpected remote kernel name %q", n)
		}
		want[n] = true
	}
	for n, seen := range want {
		if !seen {
			t.Fatalf("remote kernel name %q is missing", n)
		}
	}
}

// Every name the bootstrap may meet has to be probed on PATH, or the kernel is
// invisible to it.
func TestLocateProbesEveryRemoteKernelName(t *testing.T) {
	cmd := LocateCommand("/home/x/.reasonix/remote/bin/reasonix", LaunchFlags(true))
	for _, n := range remoteKernelNames {
		if !strings.Contains(cmd, "command -v "+n) {
			t.Errorf("LocateCommand does not probe %q on PATH:\n%s", n, cmd)
		}
		if !strings.Contains(cmd, "$P/bin/"+n) {
			t.Errorf("LocateCommand does not probe the npm prefix for %q:\n%s", n, cmd)
		}
	}
}

// The process match decides whether a running serve is this connect's own.
// Missing a name means a serve this bootstrap started is somebody else's, and
// one it cannot stop is one it keeps starting beside.
func TestServeProcessMatchCoversEveryRemoteKernelName(t *testing.T) {
	paths := StatePaths{TokenFile: "/state/ws.token", PortFile: "/state/ws.port"}
	alive := ServeAliveCommand(99, paths)
	stop := StopCommand(99, paths)
	for _, n := range remoteKernelNames {
		glob := "*" + n + "*serve*"
		if !strings.Contains(alive, glob) {
			t.Errorf("ServeAliveCommand does not match %q:\n%s", glob, alive)
		}
		if !strings.Contains(stop, glob) {
			t.Errorf("StopCommand does not match %q:\n%s", glob, stop)
		}
	}
}

// The npm install names a package that exists. The registry is not this
// project's to publish to, so a name that is not published turns every
// install into a 404 and pushes the bootstrap down the download path.
func TestNPMInstallNamesAPackageThatExists(t *testing.T) {
	if !strings.Contains(npmInstallCommand, "npm i -g reasonix") {
		t.Fatalf("npm install command = %q, want the upstream package name", npmInstallCommand)
	}
}

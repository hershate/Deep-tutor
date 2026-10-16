package serve

import (
	"net/http"
	"path/filepath"
	"time"

	"reasonix/internal/platform/crashreport"
)

// codeNoLogs answers the logs route on a kernel whose shell stated no logs
// directory: a server with no window around it has no such place to name.
const codeNoLogs = "studio.no_logs"

type studioLogs struct {
	Dir      string          `json:"dir"`
	CrashDir string          `json:"crashDir"`
	Crashes  []studioCrashes `json:"crashes"`
	// Truncated says more crash files exist than are listed.
	Truncated bool `json:"truncated"`
}

type studioCrashes struct {
	Name  string `json:"name"`
	At    string `json:"at"`
	Bytes int64  `json:"bytes"`
}

// readStudioLogs says where this install keeps its logs and which crash files
// are waiting there. The shell owns the location and tells the kernel at launch;
// answering from here keeps one source for the settings page and for a person
// asked where to look.
func (h *Hub) readStudioLogs(w http.ResponseWriter, _ *http.Request) {
	if h.opts.LogsDir == "" {
		refuse(w, http.StatusNotFound, codeNoLogs, "no logs directory was declared for this kernel", nil)
		return
	}
	crashDir := filepath.Join(h.opts.LogsDir, "crash")
	out := studioLogs{Dir: h.opts.LogsDir, CrashDir: crashDir, Crashes: []studioCrashes{}}
	files, truncated := crashreport.ListFatalLogs(crashDir)
	out.Truncated = truncated
	for _, c := range files {
		out.Crashes = append(out.Crashes, studioCrashes{Name: c.Name, At: c.At.UTC().Format(time.RFC3339), Bytes: c.Bytes})
	}
	writeJSON(w, out)
}

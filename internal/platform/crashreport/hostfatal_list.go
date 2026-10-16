package crashreport

import (
	"os"
	"path/filepath"
	"sort"
	"time"
)

// FatalLogInfo describes one crash file a host left behind.
type FatalLogInfo struct {
	Name  string
	At    time.Time
	Bytes int64
}

// MaxListedFatalLogs bounds what one listing returns.
const MaxListedFatalLogs = 50

// ListFatalLogs reads the host crash files in dir, newest first, without
// opening them, at most MaxListedFatalLogs of them; truncated says some were
// left out. An unreadable or missing dir lists nothing.
func ListFatalLogs(dir string) (list []FatalLogInfo, truncated bool) {
	names, _ := filepath.Glob(filepath.Join(dir, hostFatalPrefix+"*"+fatalSuffix))
	var out []FatalLogInfo
	for _, path := range names {
		info, err := os.Stat(path)
		if err != nil || info.IsDir() || info.Size() == 0 {
			continue
		}
		out = append(out, FatalLogInfo{Name: filepath.Base(path), At: info.ModTime(), Bytes: info.Size()})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].At.After(out[j].At) })
	if len(out) > MaxListedFatalLogs {
		return out[:MaxListedFatalLogs], true
	}
	return out, false
}

package memory

import (
	"os"
	"path/filepath"
	"slices"
	"strings"

	"reasonix/internal/base/fileutil"
	fileencoding "reasonix/internal/base/fileutil/encoding"
)

// memoryFault, when non-nil, may fail one durable step. It is the single test
// seam for this package: production leaves it nil, and tests set it through
// withFault, which restores the previous value.
var memoryFault func(op, path string) error

func fault(op, path string) error {
	if memoryFault == nil {
		return nil
	}
	return memoryFault(op, path)
}

// readIndexIn treats a missing index as empty, but does not hide other read errors.
func readIndexIn(dir string) ([]byte, error) {
	if err := fault("read-index", filepath.Join(dir, indexFile)); err != nil {
		return nil, err
	}
	existing, err := fileencoding.ReadFileUTF8(filepath.Join(dir, indexFile))
	if os.IsNotExist(err) {
		return nil, nil
	}
	return existing, err
}

// indexLinesExceptIn returns the managed MEMORY.md lines keyed by filename stem,
// dropping the entry for name and reporting whether it was present.
func indexLinesExceptIn(dir, name string) (map[string]string, bool, error) {
	existing, err := readIndexIn(dir)
	if err != nil {
		return nil, false, err
	}
	keep := map[string]string{}
	contains := false
	for line := range strings.SplitSeq(string(existing), "\n") {
		if mt := indexLineRe.FindStringSubmatch(line); mt != nil {
			if mt[1] == name {
				contains = true
			} else {
				keep[mt[1]] = strings.TrimRight(line, "\r")
			}
		}
	}
	return keep, contains, nil
}

// flushIndexIn rewrites MEMORY.md in the given directory from the managed lines,
// preserving hand-written content. Managed lines are updated or removed, and
// new managed entries are appended in sorted order.
func flushIndexIn(dir string, lines map[string]string) error {
	if err := fault("publish-index", filepath.Join(dir, indexFile)); err != nil {
		return err
	}
	path := filepath.Join(dir, indexFile)
	existing, err := readIndexIn(dir)
	if err != nil {
		return err
	}
	processed := map[string]bool{}
	var preserved strings.Builder
	preservedEmpty := true
	for line := range strings.SplitSeq(string(existing), "\n") {
		trimmed := strings.TrimRight(line, "\r")
		if mt := indexLineRe.FindStringSubmatch(trimmed); mt != nil {
			name := mt[1]
			if fresh, ok := lines[name]; ok {
				preserved.WriteString(fresh)
				preserved.WriteString("\n")
				processed[name] = true
				preservedEmpty = false
			}
			continue
		}
		preserved.WriteString(trimmed)
		preserved.WriteString("\n")
		if strings.TrimSpace(trimmed) != "" {
			preservedEmpty = false
		}
	}

	names := make([]string, 0, len(lines))
	for n := range lines {
		if !processed[n] {
			names = append(names, n)
		}
	}
	slices.Sort(names)

	var b strings.Builder
	if preservedEmpty && len(names) > 0 {
		b.WriteString("# Memory\n\n")
	} else {
		b.WriteString(preserved.String())
	}
	for _, n := range names {
		b.WriteString(lines[n])
		b.WriteString("\n")
	}
	result := strings.TrimRight(b.String(), "\n")
	if result != "" {
		result += "\n"
	}
	// The index is derived state, but a torn write would still hide facts
	// from the next session's prefix until the next reindex.
	return fileutil.AtomicWriteFile(path, []byte(result), 0o644)
}

// reindexIn rewrites the MEMORY.md line for name in the given directory,
// preserving every other managed line.
func reindexIn(dir, name string, m Memory) error {
	lines, _, err := indexLinesExceptIn(dir, name)
	if err != nil {
		return err
	}
	lines[name] = renderIndexLine(name, m)
	return flushIndexIn(dir, lines)
}

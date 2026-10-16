package installsource

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
)

const mcpHead = `{"mcpServers":{"counter":{"url":"https://example.com/mcp"}}}`

func writeSized(t *testing.T, name string, size int) string {
	t.Helper()
	path := filepath.Join(testenv.TempDir(t), name)
	if err := os.WriteFile(path, []byte(mcpHead+strings.Repeat(" ", size-len(mcpHead))), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// Local manifests are held to the size a fetched one is, on the read itself.
func TestLocalMCPJSONSizeBoundary(t *testing.T) {
	for _, size := range []int{defaultFetchLimit, defaultFetchLimit + 1} {
		entries, _, err := readMCPJSON(writeSized(t, ".mcp.json", size))
		if size > defaultFetchLimit {
			if !errors.Is(err, ErrSourceUnreadable) || len(entries) != 0 {
				t.Fatalf("size %d: entries=%d err=%v, want ErrSourceUnreadable", size, len(entries), err)
			}
			continue
		}
		if err != nil || len(entries) != 1 {
			t.Fatalf("size %d: entries=%d err=%v, want the file read", size, len(entries), err)
		}
	}
}

func TestSkillAndPluginManifestReadsAreBoundedToo(t *testing.T) {
	big := filepath.Join(testenv.TempDir(t), "SKILL.md")
	if err := os.WriteFile(big, []byte("---\nname: x\ndescription: y\n---\n"+strings.Repeat("x", defaultFetchLimit)), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readSkillFile(big, "x", false); !errors.Is(err, ErrSourceUnreadable) {
		t.Fatalf("readSkillFile err = %v, want ErrSourceUnreadable", err)
	}
	if _, err := readBounded(big); !errors.Is(err, ErrSourceUnreadable) {
		t.Fatalf("readBounded err = %v, want ErrSourceUnreadable", err)
	}
}

// A file that grows after any earlier size check is still cut at the cap.
func TestBoundedReadCapsAFileThatGrowsWhileRead(t *testing.T) {
	path := writeSized(t, ".mcp.json", 1024)
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(strings.Repeat(" ", defaultFetchLimit)); err != nil {
		t.Fatal(err)
	}
	if _, _, err := readMCPJSON(path); !errors.Is(err, ErrSourceUnreadable) {
		t.Fatalf("grown file: err = %v, want ErrSourceUnreadable", err)
	}
}

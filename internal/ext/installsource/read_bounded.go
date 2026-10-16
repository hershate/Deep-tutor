package installsource

import (
	"io"
	"os"

	fileencoding "reasonix/internal/base/fileutil/encoding"
)

// readBounded reads one local manifest in a single pass capped at
// defaultFetchLimit, the size a fetched manifest is held to. The cap sits on the
// read itself, so a device, a FIFO or a file that grows while it is read cannot
// pass a size check made earlier. Over the cap is ErrSourceUnreadable.
func readBounded(path string) ([]byte, error) {
	// codeql[go/path-injection] path is a manifest inside the source the person asked to install; reading it is the feature, and the read is capped.
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, defaultFetchLimit+1))
	if err != nil {
		return nil, err
	}
	if len(b) > defaultFetchLimit {
		return nil, newErr(ErrSourceUnreadable, "%s is larger than %d bytes", path, defaultFetchLimit)
	}
	return b, nil
}

// readBoundedUTF8 is readBounded with the file's encoding folded to UTF-8.
func readBoundedUTF8(path string) ([]byte, error) {
	b, err := readBounded(path)
	if err != nil {
		return nil, err
	}
	return fileencoding.DecodeToUTF8(b), nil
}

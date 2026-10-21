//go:build windows

package builtin

import "deep-tutor/internal/base/fileutil"

const pathSeparators = `/\`

func resolveExisting(p string) (string, error) { return fileutil.ResolveExistingPath(p) }

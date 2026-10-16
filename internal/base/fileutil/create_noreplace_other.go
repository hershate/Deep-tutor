//go:build !darwin && !linux && !windows

package fileutil

import "errors"

func RenameNoReplace(string, string) error { return errors.ErrUnsupported }

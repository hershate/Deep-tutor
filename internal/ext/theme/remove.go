package theme

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// ErrNotInstalled is a removal of a pack the user's library does not hold:
// shipped packs and plugin-contributed ones are not the library's to delete.
var ErrNotInstalled = errors.New("theme: not in the user library")

// CanRemove reports whether Remove would act on id, so a caller can settle
// what depends on the pack before the files go.
func CanRemove(id string) error {
	if err := validID(id); err != nil {
		return err
	}
	if isPluginID(id) {
		return fmt.Errorf("%w: %q comes from a plugin", ErrNotInstalled, id)
	}
	if _, err := os.Stat(filepath.Join(Dir(), id, manifestName)); err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("%w: %q", ErrNotInstalled, id)
		}
		return err
	}
	return nil
}

// Shipped reports whether a pack of this id ships with the app, so removing a
// library copy of it leaves something to load.
func Shipped(id string) bool {
	_, err := builtin.ReadFile("builtin/" + id + ".json")
	return validID(id) == nil && err == nil
}

// Remove deletes a pack from the user's library. A shipped pack of the same id
// shows again afterwards, which is what removing a copy of it means.
func Remove(id string) error {
	if err := CanRemove(id); err != nil {
		return err
	}
	return os.RemoveAll(filepath.Join(Dir(), id))
}

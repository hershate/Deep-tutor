package control

import (
	"os"
	"path"
	"path/filepath"
	"slices"

	"reasonix/internal/contract/event"
)

// StoredAttachments lists the images under .reasonix/attachments that the
// message references. It reads the references through detectRefs, the same
// resolution that decides what the model is shown, and returns each file once
// under its normalized workspace-relative path.
func (c *Controller) StoredAttachments(text string) []event.Attachment {
	var out []event.Attachment
	for _, r := range c.detectRefs(text) {
		if r.baseDir != "" || !isImageAttachmentRef(r.path) {
			continue
		}
		rel := path.Clean(filepath.ToSlash(r.path))
		if !isAttachmentRef(rel) || !storedAttachmentExists(c.workspaceRoot, rel) {
			continue
		}
		if !slices.ContainsFunc(out, func(a event.Attachment) bool { return a.Path == rel }) {
			out = append(out, event.Attachment{Path: rel})
		}
	}
	return out
}

// storedAttachmentExists is the read-only half of cleanAttachmentPath: a
// history read must never create the attachment directory it is looking for.
func storedAttachmentExists(base, rel string) bool {
	if base == "" {
		base = "."
	}
	root := filepath.Join(base, ".reasonix", "attachments")
	if info, err := os.Lstat(root); err != nil || info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return false
	}
	file := filepath.Join(base, filepath.FromSlash(rel))
	if rejectSymlinkComponents(file, root) != nil {
		return false
	}
	info, err := os.Lstat(file)
	return err == nil && info.Mode().IsRegular()
}

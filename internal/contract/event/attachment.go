package event

// Attachment is an image the host stored under .reasonix/attachments and a
// message references. Path is workspace-relative and slash-separated.
type Attachment struct {
	Path string
}

package tutor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"reasonix/internal/contract/tool"
)

// promoteArtifactName is the tool's registry identity, kept from Deep Tutor so
// the graft's mapping reads one-for-one.
const promoteArtifactName = "promote_artifact"

// promoteTool lifts an agent-drafted workspace file into the tutor library as
// a note or a knowledge-base document. Deep Tutor modelled the user's say-so
// as a second approve endpoint over a pending proposal; here the tool call
// itself is a writer, so the host's ordinary approval asks before it runs and
// an approved call does the promotion directly.
type promoteTool struct {
	dir     string
	workDir string
}

// NewPromoteArtifactTool returns promote_artifact bound to a tutor store
// directory and the workspace drafts are read from. The store opens per call,
// so wiring a session never creates tutor directories as a side effect.
func NewPromoteArtifactTool(dir, workDir string) tool.Tool {
	return promoteTool{dir: dir, workDir: workDir}
}

func (promoteTool) Name() string { return promoteArtifactName }

func (promoteTool) Description() string {
	return "Promote a file you drafted in this workspace into the tutor library so the user keeps it: target_kind \"note\" saves it as a tutor note (笔记), \"kb\" saves it as a parsed knowledge-base document (知识库) that tutor chat and search can cite. Use it when the user asks to keep, save, or archive a draft you wrote — not as a generic file copy. The call needs the user's approval; afterwards, tell them where the artifact landed."
}

func (promoteTool) Schema() json.RawMessage {
	return json.RawMessage(`{
	"type": "object",
	"additionalProperties": false,
	"properties": {
		"name": {"type": "string", "description": "Workspace-relative path of the draft file to promote, e.g. \"study-plan.md\"."},
		"target_kind": {"type": "string", "enum": ["note", "kb"], "description": "Where the draft lands: \"note\" (the tutor notebook, the default) or \"kb\" (the knowledge base)."}
	},
	"required": ["name"]
}`)
}

func (t promoteTool) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	var in struct {
		Name       string `json:"name"`
		TargetKind string `json:"target_kind"`
	}
	if err := json.Unmarshal(args, &in); err != nil {
		return "", fmt.Errorf("promote_artifact: invalid args: %w", err)
	}
	name := strings.TrimSpace(in.Name)
	if name == "" {
		return "", errors.New("promote_artifact: name is required")
	}
	target := in.TargetKind
	if target == "" {
		target = "note"
	}
	if target != "note" && target != "kb" {
		return "", errors.New("promote_artifact: target_kind must be note or kb")
	}
	path, err := promoteDraftPath(t.workDir, name)
	if err != nil {
		return "", err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("promote_artifact: read draft: %w", err)
	}
	if len(data) > maxMaterialBytes {
		return "", fmt.Errorf("promote_artifact: draft exceeds the %d MB limit", maxMaterialBytes>>20)
	}
	content := string(data)
	if strings.TrimSpace(content) == "" {
		return "", errors.New("promote_artifact: the draft is empty")
	}
	store, err := OpenStore(t.dir)
	if err != nil {
		return "", fmt.Errorf("promote_artifact: open tutor store: %w", err)
	}
	title := clampRunes(name, 200)
	if target == "kb" {
		svc := NewService(store, ServiceOptions{})
		doc, err := svc.KbAddText(title, content)
		if err != nil {
			return "", fmt.Errorf("promote_artifact: knowledge base: %w", err)
		}
		if doc.Meta.Deduped {
			return fmt.Sprintf("The knowledge base already holds this document (%q, id %s); nothing new was saved.", doc.Title, doc.ID), nil
		}
		return fmt.Sprintf("Saved knowledge base document %q (id %s, %d chars) from workspace file %s.", doc.Title, doc.ID, doc.Chars, name), nil
	}
	note := &Note{Title: title, Body: content}
	if err := store.SaveNote(note); err != nil {
		return "", fmt.Errorf("promote_artifact: note: %w", err)
	}
	return fmt.Sprintf("Saved tutor note %q (id %s) from workspace file %s.", note.Title, note.ID, name), nil
}

// ReadOnly is false: the call writes to the tutor library, so permission
// policy treats it as a writer and asks unless the user's mode allows.
func (promoteTool) ReadOnly() bool { return false }

// promoteDraftPath confines a promote request to a file inside the workspace,
// the way Deep Tutor confined drafts to the run's scratch directory. The
// joined-prefix check is the authority; the early Clean tests keep the common
// escapes out of the filesystem.
func promoteDraftPath(workDir, name string) (string, error) {
	if workDir == "" {
		return "", errors.New("promote_artifact: no workspace is bound to this session")
	}
	clean := filepath.Clean(strings.TrimSpace(name))
	if clean == "" || clean == "." || clean == ".." || filepath.IsAbs(clean) ||
		strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("promote_artifact: %q must name a file inside the workspace", name)
	}
	full := filepath.Join(workDir, clean)
	rel, err := filepath.Rel(workDir, full)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("promote_artifact: %q must name a file inside the workspace", name)
	}
	return full, nil
}

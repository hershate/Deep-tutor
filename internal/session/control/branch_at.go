package control

import (
	"errors"
	"fmt"
	"strings"

	"reasonix/internal/contract/provider"
	"reasonix/internal/state/sessionstore"
)

// ErrBranchUnavailable is a branch the conversation cannot be cut for: a turn it
// has no live boundary for (a resumed session), or one compaction has moved past.
var ErrBranchUnavailable = errors.New("control: this point cannot be branched")

// seedBranch writes messages into a new session file beside the current one and
// records the current session as its parent. A non-empty name becomes the title
// the sidebar shows, so the branch never reads as a second copy of its source.
func (c *Controller) seedBranch(src []provider.Message, name string) (*sessionstore.Session, string, error) {
	name = strings.TrimSpace(name)
	branched := append([]provider.Message(nil), src...)
	sess := sessionstore.NewSession("")
	sess.Messages = branched
	newPath := sessionstore.NewSessionPath(c.sessionDir, c.label)
	if err := sess.SaveIfAbsent(newPath); err != nil {
		return nil, "", err
	}
	preview, turns := sessionstore.SessionPreviewFromMessages(branched)
	if err := sessionstore.SaveBranchMeta(newPath, sessionstore.BranchMeta{
		Name:          name,
		ParentID:      sessionstore.BranchID(c.SessionPath()),
		Preview:       preview,
		Turns:         turns,
		SchemaVersion: sessionstore.BranchMetaCountsVersion,
	}); err != nil {
		return nil, "", err
	}
	if name != "" {
		if err := sessionstore.RenameSession(newPath, name); err != nil {
			return nil, "", err
		}
	}
	return sess, newPath, nil
}

// BranchAt copies the conversation as it stood at the end of turn into a new
// session file and leaves this controller on its own session, so a frontend can
// open the branch beside the source. The cut is a live turn boundary, which is
// why a resumed session or a point compaction passed is refused.
func (c *Controller) BranchAt(turn int, name string) (string, error) {
	if c.executor == nil || c.sessionDir == "" {
		return "", fmt.Errorf("%w: sessions are not persisted here", ErrBranchUnavailable)
	}
	if err := c.beginRotation(); err != nil {
		if errors.Is(err, errTurnRunningRotation) {
			return "", fmt.Errorf("cannot branch while a turn is running: %w", ErrTurnRunning)
		}
		return "", err
	}
	defer c.endRotation()
	if err := c.Snapshot(); err != nil {
		return "", err
	}
	src := c.executor.Session().Snapshot()
	end, ok := c.checkpoints.endOfTurn(turn, len(src))
	if !ok {
		return "", fmt.Errorf("%w: turn %d has no live boundary", ErrBranchUnavailable, turn)
	}
	if end > len(src) {
		return "", fmt.Errorf("%w: the conversation was compacted past turn %d", ErrBranchUnavailable, turn)
	}
	_, path, err := c.seedBranch(src[:end], name)
	if err != nil {
		return "", err
	}
	return path, nil
}

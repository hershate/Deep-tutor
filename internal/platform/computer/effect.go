package computer

import (
	"strings"

	"reasonix/internal/base/textutil"
)

// EffectClass is how far a step that reached the application is known to
// have worked. A step that did not reach it is a Failure, never a class.
type EffectClass string

const (
	EffectConfirmed     EffectClass = "confirmed"
	EffectUnverifiable  EffectClass = "unverifiable"
	EffectSuspectedNoop EffectClass = "suspected_noop"
)

// Evidence is the kind of fact the driver read that a class rests on.
type Evidence string

const (
	EvidenceValueReadback  Evidence = "value_readback"
	EvidenceValueUnchanged Evidence = "value_unchanged"
)

// Effect is what is known of a step that went through. Class is empty for a
// step that sends the application nothing, such as a wait.
type Effect struct {
	Driver   *DriverAction
	Class    EffectClass
	Evidence []Evidence
	// BlockedBy is the modal the input went to instead of the window behind it.
	BlockedBy *Modal
}

// Modal is a window that holds an application's input until it is answered.
// Title and Blocks are names shown to the model, never read for a decision.
type Modal struct {
	Ref    string `json:"ref"`
	Title  string `json:"title"`
	Blocks string `json:"blocks"`
}

// modalNameLimit is the length the helpers clip every other label in a tree
// to; a modal's names are the application's to choose, so they get no more.
var modalNameLimit = textutil.PreviewLimit{Graphemes: 160, Lines: 1}

// ShownName quotes an application-chosen name bounded, with every control and
// invisible character, and the quote itself, rendered as a visible escape.
func ShownName(s string) string {
	bounded, _ := textutil.BoundLiteral(s, modalNameLimit)
	return `"` + strings.ReplaceAll(bounded, `"`, `\u{22}`) + `"`
}

// ShownBundle prints a bundle id as it is when nothing is hidden in it, so a
// normal id stays a key the model can send back, and escaped otherwise.
func ShownBundle(s string) string {
	bounded, _ := textutil.BoundLiteral(s, modalNameLimit)
	return bounded
}

func (m Modal) String() string {
	title := m.Title
	if title == "" {
		title = "(untitled)"
	}
	s := "the modal " + ShownName(title)
	if m.Ref != "" {
		s += " [" + m.Ref + "]"
	}
	if m.Blocks != "" {
		s += " over " + ShownName(m.Blocks)
	}
	return s
}

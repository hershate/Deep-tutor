package textutil

import (
	"strings"

	"github.com/charmbracelet/x/ansi"
)

type scrub struct {
	// oneLine turns every line break and tab into a space and collapses runs.
	oneLine bool
	// joiners deletes zero-width joiners and every other format character,
	// including those that emoji and some scripts need.
	joiners bool
	// ansi removes whole escape sequences instead of only their control bytes.
	ansi bool
}

// run returns the scrubbed text and whether any non-whitespace character was
// deleted; folding whitespace is not a deletion.
func (o scrub) run(s string) (string, bool) {
	in := strings.ToValidUTF8(s, "�")
	if o.ansi {
		in = ansi.Strip(in)
	}
	removed := in != strings.ToValidUTF8(s, "�")
	rs := []rune(in)
	hidden := hiddenMask(rs)
	var b strings.Builder
	b.Grow(len(in))
	for i, r := range rs {
		switch {
		case o.oneLine && (r == '\t' || r == '\n' || r == '\r' || r == '\u2028' || r == '\u2029' || r == '\u0085'):
			b.WriteByte(' ')
		case !o.oneLine && (r == '\t' || r == '\n'):
			b.WriteRune(r)
		case hidden[i], o.joiners && isFormat(r):
			removed = true
		default:
			b.WriteRune(r)
		}
	}
	out := b.String()
	if o.oneLine {
		out = strings.Join(strings.Fields(out), " ")
	}
	return out, removed
}

// SanitizeDisplay is the display policy: one line, with escape sequences,
// controls, bidirectional overrides and zero-width characters deleted, so one
// string cannot repaint a terminal, reorder a row or smuggle a line break.
func SanitizeDisplay(s string) string {
	out, _ := scrub{oneLine: true, ansi: true}.run(s)
	return out
}

// ScrubLaunch is the launch policy for a line a person approves: display, plus
// joiners, because redaction must still match a key a zero-width character
// split. It deletes, so the second result says whether anything was, and a
// surface that shows the line must say so too.
func ScrubLaunch(s string) (string, bool) {
	return scrub{oneLine: true, joiners: true, ansi: true}.run(s)
}

// SanitizeLaunch is ScrubLaunch for a caller with no room to say what it deleted.
func SanitizeLaunch(s string) string {
	out, _ := ScrubLaunch(s)
	return out
}

// SanitizeStored is the policy for text that is stored and shown later, with
// its lines kept: controls (an escape's own bytes included) and every
// invisible character are deleted.
func SanitizeStored(s string) string {
	out, _ := scrub{joiners: true}.run(s)
	return out
}

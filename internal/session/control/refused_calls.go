package control

import (
	"fmt"
	"strings"

	"reasonix/internal/base/secrets"
	"reasonix/internal/contract/event"
	"reasonix/internal/safety/permission"
)

const refusedCallArgsLimit = 160

// noteRefusedCall owes the model a line for each call the host refused. The
// refusal reason already reached it in that call's result; this is what keeps
// the decision in view once the result has been folded away.
func (c *Controller) noteRefusedCall(t event.Tool) {
	if !permission.RefusalStands(t.RefusalCode) {
		return
	}
	args := []rune(secrets.RedactCredentials(strings.TrimSpace(t.Args)))
	suffix := ""
	if len(args) > refusedCallArgsLimit {
		args, suffix = args[:refusedCallArgsLimit], "…"
	}
	c.refused.owe(promptNoteEscaper.Replace(fmt.Sprintf("- %s %s%s (%s)", t.Name, string(args), suffix, t.RefusalCode)))
}

const refusedCallsHeader = "The host refused these tool calls since your last turn; do not retry them or a rewording of them — choose another approach or tell the user what was refused:"

func (c *Controller) refusedCallsBlock() string {
	notes := c.refused.owed()
	if notes == "" {
		return ""
	}
	return wrapTurnBlock("refused-calls", refusedCallsHeader+"\n"+notes)
}

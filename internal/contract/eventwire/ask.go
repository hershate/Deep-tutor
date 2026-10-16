package eventwire

import "reasonix/internal/contract/surfacespec"

// AskQuestion is one JSON-formatted structured ask question.
type AskQuestion struct {
	ID      string            `json:"id"`
	Header  string            `json:"header,omitempty"`
	Prompt  string            `json:"prompt" externalizable:"true"`
	Reason  string            `json:"reason,omitempty"`
	Options []AskOption       `json:"options"`
	Multi   bool              `json:"multi,omitempty"`
	Default []string          `json:"default,omitempty"`
	Surface *surfacespec.Spec `json:"surface,omitempty"`
	CallID  string            `json:"callId,omitempty"`
}

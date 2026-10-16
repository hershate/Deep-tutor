package surfacespec

import "encoding/json"

const (
	ToolName     = "render_surface"
	MaxSpecBytes = 65536
	MaxNodes     = 200
	MaxDepth     = 8
	MaxText      = 4000
	MaxLabel     = 200
	MaxFields    = 16
	MaxOptions   = 16
)

type Spec struct {
	SpecVersion int    `json:"spec_version"`
	Title       string `json:"title,omitempty"`
	Body        []Node `json:"body"`
}

type Node struct {
	Kind     string   `json:"kind"`
	Value    string   `json:"value,omitempty"`
	Key      string   `json:"key,omitempty"`
	Label    string   `json:"label,omitempty"`
	Tone     string   `json:"tone,omitempty"`
	Progress *float64 `json:"progress,omitempty"`
	ActionID string   `json:"actionId,omitempty"`
	Children []Node   `json:"children,omitempty"`
	Field    *Field   `json:"field,omitempty"`
	Diff     *Diff    `json:"diff,omitempty"`
	Dataset  *Dataset `json:"dataset,omitempty"`
}

type Diff struct {
	Before string `json:"before"`
	After  string `json:"after"`
}

type Field struct {
	Type      string          `json:"type"`
	Required  bool            `json:"required,omitempty"`
	Default   json.RawMessage `json:"default,omitempty"`
	Min       *float64        `json:"min,omitempty"`
	Max       *float64        `json:"max,omitempty"`
	MaxLength int             `json:"max_length,omitempty"`
	Options   []Option        `json:"options,omitempty"`
}

type Option struct {
	Value       string    `json:"value"`
	Label       string    `json:"label"`
	Description string    `json:"description,omitempty"`
	Preview     string    `json:"preview,omitempty"`
	Group       string    `json:"group,omitempty"`
	Location    *Location `json:"location,omitempty"`
	Price       *float64  `json:"price,omitempty"`
	Currency    string    `json:"currency,omitempty"`
	Facts       []Fact    `json:"facts,omitempty"`
	Image       *Image    `json:"image,omitempty"`
}

func (s *Spec) Canonical() ([]byte, error) { return json.Marshal(s) }

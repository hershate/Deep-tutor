package surfacespec

import (
	"fmt"
	"math"
	"strings"
	"unicode/utf8"
)

func (s *Spec) Validate() error {
	if s.SpecVersion != 1 {
		return fail(CodeSpecVersionUnsupported, "/spec_version")
	}
	if len(s.Title) > MaxLabel {
		return fail(CodeLimitExceeded, "/title")
	}
	if len(s.Body) == 0 {
		return fail(CodeSchemaInvalid, "/body")
	}
	budget := MaxNodes
	fields := map[string]*Field{}
	for i, n := range s.Body {
		if err := validateNode(n, fmt.Sprintf("/body/%d", i), 1, &budget, fields); err != nil {
			return err
		}
	}
	return s.validateActions()
}

func validateNode(n Node, path string, depth int, budget *int, fields map[string]*Field) error {
	*budget--
	if depth > MaxDepth || *budget < 0 {
		return fail(CodeLimitExceeded, path)
	}
	if len(n.Value) > MaxText || len(n.Key) > MaxLabel || len(n.Label) > MaxLabel {
		return fail(CodeLimitExceeded, path)
	}
	if n.Tone != "" && n.Tone != "dim" && n.Tone != "strong" && n.Tone != "ok" && n.Tone != "warn" && n.Tone != "err" && n.Tone != "accent" {
		return fail(CodeSchemaInvalid, path+"/tone")
	}
	allowed, err := nodeFields(n, path, fields)
	if err != nil {
		return err
	}
	present := map[string]bool{"value": n.Value != "", "key": n.Key != "", "label": n.Label != "", "tone": n.Tone != "", "progress": n.Progress != nil, "actionId": n.ActionID != "", "children": len(n.Children) != 0, "field": n.Field != nil, "diff": n.Diff != nil, "dataset": n.Dataset != nil}
	for key, exists := range present {
		if exists && !containsToken(allowed, key) {
			return fail(CodeSchemaInvalid, path+"/"+key)
		}
	}
	for i, child := range n.Children {
		if err := validateNode(child, fmt.Sprintf("%s/children/%d", path, i), depth+1, budget, fields); err != nil {
			return err
		}
	}
	return nil
}

func nodeFields(n Node, path string, fields map[string]*Field) (string, error) {
	allowed := ""
	switch n.Kind {
	case "text", "markdown":
		allowed = "value tone"
		if n.Value == "" {
			return "", fail(CodeSchemaInvalid, path+"/value")
		}
	case "kv":
		allowed = "key value tone"
		if n.Key == "" {
			return "", fail(CodeSchemaInvalid, path+"/key")
		}
	case "pip":
		allowed = "value tone"
		if n.Tone == "" {
			return "", fail(CodeSchemaInvalid, path+"/tone")
		}
	case "meter":
		allowed = "label progress tone"
		if n.Progress == nil || !finite(*n.Progress) || *n.Progress < 0 || *n.Progress > 1 {
			return "", fail(CodeValueInvalid, path+"/progress")
		}
	case "button":
		if !identifier(n.ActionID) || n.Label == "" {
			return "", fail(CodeSchemaInvalid, path)
		}
		allowed = "label actionId"
	case "divider":
	case "row", "stack":
		allowed = "children"
		if len(n.Children) == 0 {
			return "", fail(CodeSchemaInvalid, path+"/children")
		}
	case "diff_preview":
		if n.Diff == nil {
			return "", fail(CodeSchemaInvalid, path+"/diff")
		}
		if len(n.Diff.Before) > MaxText || len(n.Diff.After) > MaxText {
			return "", fail(CodeLimitExceeded, path+"/diff")
		}
		allowed = "label diff"
	case "dashboard":
		return "label dataset", validateDataset(n.Dataset, path+"/dataset")
	case "field", "choice_card", "editable_text", "map", "comparison":
		return "key label field", validateInput(n, path, fields)
	default:
		return "", fail(CodeComponentUnknown, path+"/kind")
	}
	return allowed, nil
}

func validateInput(n Node, path string, fields map[string]*Field) error {
	if !identifier(n.Key) || n.Label == "" || n.Field == nil {
		return fail(CodeSchemaInvalid, path)
	}
	if _, exists := fields[n.Key]; exists {
		return fail(CodeSchemaInvalid, path+"/key")
	}
	fields[n.Key] = n.Field
	if len(fields) > MaxFields {
		return fail(CodeLimitExceeded, path)
	}
	if n.Kind == "choice_card" && n.Field.Type != "choice" && n.Field.Type != "multichoice" {
		return fail(CodeSchemaInvalid, path+"/field/type")
	}
	if n.Kind == "editable_text" && n.Field.Type != "string" {
		return fail(CodeSchemaInvalid, path+"/field/type")
	}
	if err := validateRichInput(n, path); err != nil {
		return err
	}
	return validateField(n.Field, path+"/field")
}

func containsToken(tokens, key string) bool {
	for token := range strings.FieldsSeq(tokens) {
		if token == key {
			return true
		}
	}
	return false
}

func identifier(value string) bool {
	if len(value) == 0 || len(value) > 64 {
		return false
	}
	for _, c := range value {
		if c != '_' && c != '-' && (c < 'a' || c > 'z') && (c < 'A' || c > 'Z') && (c < '0' || c > '9') {
			return false
		}
	}
	return true
}

func finite(n float64) bool { return !math.IsNaN(n) && !math.IsInf(n, 0) }

func validateField(f *Field, path string) error {
	switch f.Type {
	case "string", "number", "bool", "choice", "multichoice":
	default:
		return fail(CodeSchemaInvalid, path+"/type")
	}
	if f.Min != nil && (!finite(*f.Min) || f.Type != "number") || f.Max != nil && (!finite(*f.Max) || f.Type != "number") {
		return fail(CodeSchemaInvalid, path)
	}
	if f.Min != nil && f.Max != nil && *f.Min > *f.Max {
		return fail(CodeSchemaInvalid, path)
	}
	if f.MaxLength < 0 || f.MaxLength > MaxText || f.MaxLength != 0 && f.Type != "string" {
		return fail(CodeSchemaInvalid, path+"/max_length")
	}
	choice := f.Type == "choice" || f.Type == "multichoice"
	if choice && len(f.Options) == 0 || !choice && len(f.Options) != 0 {
		return fail(CodeSchemaInvalid, path+"/options")
	}
	if len(f.Options) > MaxOptions {
		return fail(CodeLimitExceeded, path+"/options")
	}
	seen := map[string]bool{}
	for i, o := range f.Options {
		p := fmt.Sprintf("%s/options/%d", path, i)
		if !identifier(o.Value) || o.Label == "" || seen[o.Value] {
			return fail(CodeSchemaInvalid, p)
		}
		seen[o.Value] = true
		if err := validateRichOption(o, p); err != nil {
			return err
		}
		if len(o.Label) > MaxLabel || len(o.Description) > MaxText || len(o.Preview) > MaxText {
			return fail(CodeLimitExceeded, p)
		}
	}
	if len(f.Default) != 0 {
		return f.validateValue(f.Default, path+"/default")
	}
	return nil
}

func stringLength(s string) int { return utf8.RuneCountInString(s) }

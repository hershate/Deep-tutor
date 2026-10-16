package surfacespec

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"slices"
)

func (s *Spec) ParseAnswers(raw []byte) (map[string]json.RawMessage, error) {
	if len(raw) > MaxSpecBytes || uniqueKeys(raw) != nil {
		return nil, fail(CodeValueInvalid, "/answers")
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	var answers map[string]json.RawMessage
	if err := dec.Decode(&answers); err != nil || answers == nil {
		return nil, fail(CodeValueInvalid, "/answers")
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, fail(CodeValueInvalid, "/answers")
	}
	if err := s.ValidateAnswers(answers); err != nil {
		return nil, err
	}
	return answers, nil
}

func (s *Spec) Fields() map[string]*Field {
	fields := map[string]*Field{}
	var walk func([]Node)
	walk = func(nodes []Node) {
		for _, n := range nodes {
			if n.Field != nil {
				fields[n.Key] = n.Field
			}
			walk(n.Children)
		}
	}
	walk(s.Body)
	return fields
}

func (s *Spec) ValidateAnswers(answers map[string]json.RawMessage) error {
	if err := s.Validate(); err != nil {
		return err
	}
	fields := s.Fields()
	for key, value := range answers {
		f, ok := fields[key]
		if !ok {
			return fail(CodeValueInvalid, "/answers/"+key)
		}
		if err := f.validateValue(value, "/answers/"+key); err != nil {
			return err
		}
	}
	for key, f := range fields {
		if _, present := answers[key]; !present && f.Required {
			return fail(CodeValueInvalid, "/answers/"+key)
		}
	}
	return nil
}

func (f *Field) validateValue(raw json.RawMessage, path string) error {
	if len(raw) > MaxText*6+2 {
		return fail(CodeValueInvalid, path)
	}
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return fail(CodeValueInvalid, path)
	}
	ok := false
	switch f.Type {
	case "string":
		v, yes := value.(string)
		limit := f.MaxLength
		if limit == 0 {
			limit = MaxText
		}
		ok = yes && stringLength(v) <= limit && (!f.Required || v != "")
	case "number":
		v, yes := value.(float64)
		ok = yes && finite(v) && (f.Min == nil || v >= *f.Min) && (f.Max == nil || v <= *f.Max)
	case "bool":
		_, ok = value.(bool)
	case "choice":
		v, yes := value.(string)
		ok = yes && f.hasChoice(v)
	case "multichoice":
		vs, yes := value.([]any)
		if !yes || len(vs) > len(f.Options) || f.Required && len(vs) == 0 {
			break
		}
		seen := map[string]bool{}
		ok = true
		for _, value := range vs {
			v, yes := value.(string)
			if !yes || !f.hasChoice(v) || seen[v] {
				ok = false
				break
			}
			seen[v] = true
		}
	}
	if !ok {
		return fail(CodeValueInvalid, path)
	}
	return nil
}

func (f *Field) hasChoice(value string) bool {
	return slices.ContainsFunc(f.Options, func(o Option) bool { return o.Value == value })
}

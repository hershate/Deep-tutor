package surfacespec

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

const form = `{"spec_version":1,"title":"Configure","body":[{"kind":"stack","children":[{"kind":"text","value":"Choose settings"},{"kind":"field","key":"count","label":"Count","field":{"type":"number","required":true,"min":1,"max":5,"default":2}},{"kind":"choice_card","key":"mode","label":"Mode","field":{"type":"choice","options":[{"value":"fast","label":"Fast","preview":"Short run"},{"value":"full","label":"Full"}]}}]}]}`

func TestParseAndValidateTypedAnswers(t *testing.T) {
	s, err := Parse([]byte(form))
	if err != nil {
		t.Fatal(err)
	}
	valid := map[string]json.RawMessage{"count": json.RawMessage(`3`), "mode": json.RawMessage(`"fast"`)}
	if err := s.ValidateAnswers(valid); err != nil {
		t.Fatal(err)
	}
	for name, answers := range map[string]map[string]json.RawMessage{
		"wrong type":       {"count": json.RawMessage(`"3"`)},
		"outside bounds":   {"count": json.RawMessage(`6`)},
		"missing required": {},
		"unknown key":      {"count": json.RawMessage(`3`), "approve": json.RawMessage(`true`)},
		"unknown choice":   {"count": json.RawMessage(`3`), "mode": json.RawMessage(`"other"`)},
		"null":             {"count": json.RawMessage(`null`)},
	} {
		t.Run(name, func(t *testing.T) {
			if err := s.ValidateAnswers(answers); !errors.Is(err, ErrValueInvalid) {
				t.Fatalf("got %v", err)
			}
		})
	}
	canonical, err := s.Canonical()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Parse(canonical); err != nil {
		t.Fatal(err)
	}
}

func TestHostileSurfaceSpecs(t *testing.T) {
	for name, raw := range map[string]string{
		"html":                `{"spec_version":1,"body":[{"kind":"html","value":"<script/>"}]}`,
		"unknown property":    strings.Replace(form, `"title":"Configure"`, `"url":"https://example.com"`, 1),
		"duplicate property":  strings.Replace(form, `"spec_version":1`, `"spec_version":1,"SPEC_VERSION":1`, 1),
		"action":              `{"spec_version":1,"body":[{"kind":"button","label":"Approve","actionId":"approve","operation":"approve"}]}`,
		"duplicate field":     strings.Replace(form, `"key":"mode"`, `"key":"count"`, 1),
		"bad default":         strings.Replace(form, `"default":2`, `"default":"2"`, 1),
		"leaf children":       `{"spec_version":1,"body":[{"kind":"text","value":"hi","children":[{"kind":"text","value":"hidden"}]}]}`,
		"wrong kind property": `{"spec_version":1,"body":[{"kind":"text","value":"hi","actionId":"approve"}]}`,
		"trailing document":   form + `{}`,
		"empty action":        `{"spec_version":1,"body":[{"kind":"text","value":"hi","actionId":""}]}`,
		"empty leaf children": `{"spec_version":1,"body":[{"kind":"text","value":"hi","children":[]}]}`,
		"null property":       `{"spec_version":1,"body":[{"kind":"text","value":"hi","tone":null}]}`,
		"wrong case":          `{"spec_version":1,"body":[{"Kind":"text","value":"hi"}]}`,
	} {
		t.Run(name, func(t *testing.T) {
			if s, err := Parse([]byte(raw)); err == nil || s != nil {
				t.Fatalf("accepted %s", raw)
			}
		})
	}
	if _, err := Parse([]byte(strings.Replace(form, `"spec_version":1`, `"spec_version":2`, 1))); !errors.Is(err, ErrSpecVersionUnsupported) {
		t.Fatal(err)
	}
	if _, err := Parse([]byte(strings.Repeat(" ", MaxSpecBytes+1))); !errors.Is(err, ErrLimitExceeded) {
		t.Fatal(err)
	}
}

func TestSurfaceDepthAndNodeBounds(t *testing.T) {
	leaf := `{"kind":"text","value":"x"}`
	nested := leaf
	for range MaxDepth {
		nested = `{"kind":"stack","children":[` + nested + `]}`
	}
	for _, body := range []string{nested, strings.TrimSuffix(strings.Repeat(leaf+",", MaxNodes+1), ",")} {
		if _, err := Parse([]byte(`{"spec_version":1,"body":[` + body + `]}`)); !errors.Is(err, ErrLimitExceeded) {
			t.Fatalf("got %v", err)
		}
	}
}

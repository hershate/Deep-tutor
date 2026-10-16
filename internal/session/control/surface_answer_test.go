package control

import (
	"encoding/json"
	"errors"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/safety/permission"
)

func TestSurfaceAnswerRefusalKeepsPendingPrompt(t *testing.T) {
	s, err := surfacespec.Parse([]byte(`{"spec_version":1,"body":[{"kind":"field","key":"count","label":"Count","field":{"type":"number","required":true,"min":1,"max":5}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	c := &Controller{}
	c.approval = newApprovalManager(permission.Policy{}, "", 0, false)
	reply := c.approval.registerAsk("ask-1", []event.AskQuestion{{ID: "surface", Surface: s}}, nil)
	for _, value := range []string{`{"count":"2"}`, `{"count":6}`, `{"approve":true}`, `{}`, `null`, `{"count":2,"count":3}`} {
		if err := c.AnswerQuestionFrom("ask-1", []event.AskAnswer{{QuestionID: "surface", Value: json.RawMessage(value)}}, nil); !errors.Is(err, surfacespec.ErrValueInvalid) {
			t.Fatalf("%s: %v", value, err)
		}
		select {
		case <-reply:
			t.Fatal("refusal resolved prompt")
		default:
		}
	}
	if err := c.AnswerQuestionFrom("ask-1", []event.AskAnswer{{QuestionID: "surface", Value: json.RawMessage(`{"count":2}`)}}, nil); err != nil {
		t.Fatal(err)
	}
	if got := <-reply; string(got[0].Value) != `{"count":2}` {
		t.Fatal(got)
	}
	if err := c.AnswerQuestionFrom("ask-1", nil, nil); !errors.Is(err, surfacespec.ErrExpired) {
		t.Fatalf("duplicate: %v", err)
	}
}

func TestSurfaceLegacyAnswerCannotBypassValidation(t *testing.T) {
	s, _ := surfacespec.Parse([]byte(`{"spec_version":1,"body":[{"kind":"field","key":"name","label":"Name","field":{"type":"string","required":true}}]}`))
	c := &Controller{}
	c.approval = newApprovalManager(permission.Policy{}, "", 0, false)
	reply := c.approval.registerAsk("ask-1", []event.AskQuestion{{ID: "surface", Surface: s}}, nil)
	c.AnswerQuestion("ask-1", []event.AskAnswer{{QuestionID: "surface", Selected: []string{"invalid transport"}}})
	select {
	case <-reply:
		t.Fatal("legacy answer bypassed schema")
	default:
	}
	if err := c.AnswerQuestionFrom("ask-1", nil, nil); err != nil {
		t.Fatal(err)
	}
	if got := <-reply; len(got) != 0 {
		t.Fatal(got)
	}
}

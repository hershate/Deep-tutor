package surfacespec

import (
	"errors"
	"testing"
)

func TestSurfaceActionButtonAndFields(t *testing.T) {
	raw := []byte(`{"spec_version":1,"body":[{"kind":"editable_text","key":"draft","label":"Draft","field":{"type":"string","required":true}},{"kind":"button","label":"Continue","actionId":"continue"}]}`)
	s, err := Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	if len(s.Actions()) != 1 {
		t.Fatal("action absent")
	}
	values, err := s.ActionValues("continue", []byte(`{"draft":"  text\n"}`))
	if err != nil || string(values["draft"]) != `"  text\n"` {
		t.Fatalf("values: %v %v", values, err)
	}
	for _, action := range []string{"missing", "grant_permissions"} {
		if _, err := s.ActionValues(action, []byte(`{"draft":"text"}`)); !errors.Is(err, ErrValueInvalid) {
			t.Fatalf("undeclared action: %v", err)
		}
	}
}

func TestSurfaceActionsCannotExpressHostOperations(t *testing.T) {
	for _, raw := range []string{
		`{"spec_version":1,"body":[{"kind":"button","label":"Run","actionId":"run","approve":true}]}`,
		`{"spec_version":1,"body":[{"kind":"button","label":"Run","actionId":"run","command":"shell"}]}`,
		`{"spec_version":1,"body":[{"kind":"button","label":"A","actionId":"same"},{"kind":"button","label":"B","actionId":"same"}]}`,
	} {
		if _, err := Parse([]byte(raw)); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}

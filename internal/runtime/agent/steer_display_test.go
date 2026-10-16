package agent

import "testing"

func TestSteerDisplayedItemKeepsPayloadSeparate(t *testing.T) {
	a := &Agent{}
	a.steer.open()
	if !a.SteerDisplayedItemFrom("item", func() (string, error) { return "<surface_action>payload</surface_action>", nil }, "Continue", nil) {
		t.Fatal("rejected steer")
	}
	text, entry, ok := a.consumeSteer()
	if !ok || text != "<surface_action>payload</surface_action>" || entry.display != "Continue" {
		t.Fatalf("%q %+v %v", text, entry, ok)
	}
}

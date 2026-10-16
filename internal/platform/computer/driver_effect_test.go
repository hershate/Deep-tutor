package computer

import "testing"

func TestCUALegacyAXValueRetainsUnconfirmedEffect(t *testing.T) {
	for _, name := range []string{"set_value", "click"} {
		action := DriverAction{Effect: "confirmed", Path: "ax"}.nativeContract(name)
		done, err := finishAction(action)
		if name == "set_value" {
			if err != nil || done.Effect.Class != EffectUnverifiable || done.Effect.Driver.Path != "ax" {
				t.Fatalf("legacy value: %+v %v", done, err)
			}
		} else if CodeOf(err) != CodeFailed {
			t.Fatalf("unrecognized legacy action: %+v %v", done, err)
		}
	}
}

func TestCUAWindowChangeDoesNotProveTargetEffect(t *testing.T) {
	action := DriverAction{Effect: "confirmed", Route: "accessibility"}
	action.Evidence = append(action.Evidence, struct {
		Kind   string `json:"kind"`
		Detail string `json:"detail"`
	}{Kind: "window_change"})
	e := action.project()
	if e.Class != EffectUnverifiable || e.Driver == nil {
		t.Fatalf("%+v", e)
	}
	action.Evidence[0].Kind = "value_readback"
	if action.project().Class != EffectConfirmed {
		t.Fatal("lost readback evidence")
	}
}

func TestCUAPartialDeliveryRetainsItsCount(t *testing.T) {
	count := 2
	action := DriverAction{Effect: "partial", Route: "synthetic_events"}
	action.Delivery = &struct {
		Mode  string `json:"mode"`
		Count *int   `json:"delivered_count"`
	}{Mode: "background", Count: &count}
	e := action.project()
	if e.Class != EffectUnverifiable || *e.Driver.Delivery.Count != 2 {
		t.Fatal(e)
	}
}

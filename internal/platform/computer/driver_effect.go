package computer

// DriverAction retains CUA's delivery facts without upgrading them to target proof.
type DriverAction struct {
	Effect   string `json:"effect"`
	Route    string `json:"route"`
	Path     string `json:"path,omitempty"`
	Summary  string `json:"summary"`
	Evidence []struct {
		Kind   string `json:"kind"`
		Detail string `json:"detail"`
	} `json:"evidence"`
	Delivery *struct {
		Mode  string `json:"mode"`
		Count *int   `json:"delivered_count"`
	} `json:"delivery"`
}

func (a DriverAction) nativeContract(name string) DriverAction {
	if name == "set_value" && a.Route == "" && a.Path == "ax" {
		a.Route = "accessibility"
	}
	return a
}

func (a DriverAction) project() Effect {
	e := Effect{Class: EffectUnverifiable, Driver: &a}
	if a.Effect == "confirmed" {
		for _, ev := range a.Evidence {
			if ev.Kind == "value_readback" {
				e.Class = EffectConfirmed
				e.Evidence = []Evidence{EvidenceValueReadback}
				break
			}
		}
	}
	return e
}

package surfacespec

type Location struct {
	Latitude  *float64 `json:"latitude"`
	Longitude *float64 `json:"longitude"`
}

type Fact struct {
	Label string `json:"label"`
	Value string `json:"value"`
}

type Dataset struct {
	Categories []string `json:"categories"`
	Series     []Series `json:"series"`
}

type Series struct {
	ID     string    `json:"id"`
	Label  string    `json:"label"`
	Unit   string    `json:"unit,omitempty"`
	Values []float64 `json:"values"`
}

func validateRichOption(o Option, path string) error {
	if !validImage(o.Image) {
		return fail(CodeValueInvalid, path+"/image")
	}
	if len(o.Group) > MaxLabel || len(o.Currency) > 8 || len(o.Facts) > 12 {
		return fail(CodeLimitExceeded, path)
	}
	if o.Price != nil && (!finite(*o.Price) || *o.Price < 0 || o.Currency == "") {
		return fail(CodeValueInvalid, path+"/price")
	}
	if o.Location != nil {
		p := o.Location
		if p.Latitude == nil || p.Longitude == nil || !finite(*p.Latitude) || !finite(*p.Longitude) || *p.Latitude < -85 || *p.Latitude > 85 || *p.Longitude < -180 || *p.Longitude > 180 {
			return fail(CodeValueInvalid, path+"/location")
		}
	}
	seen := map[string]bool{}
	for _, f := range o.Facts {
		if f.Label == "" || seen[f.Label] || len(f.Label) > MaxLabel || len(f.Value) > MaxText {
			return fail(CodeSchemaInvalid, path+"/facts")
		}
		seen[f.Label] = true
	}
	return nil
}

func validateRichInput(n Node, path string) error {
	if n.Kind == "map" {
		if n.Field.Type != "multichoice" {
			return fail(CodeSchemaInvalid, path+"/field/type")
		}
		for _, o := range n.Field.Options {
			if o.Location == nil {
				return fail(CodeSchemaInvalid, path+"/field/options")
			}
		}
	}
	if n.Kind == "comparison" && n.Field.Type != "choice" && n.Field.Type != "multichoice" {
		return fail(CodeSchemaInvalid, path+"/field/type")
	}
	return nil
}

func validateDataset(d *Dataset, path string) error {
	if d == nil || len(d.Categories) == 0 || len(d.Categories) > 64 || len(d.Series) == 0 || len(d.Series) > 8 {
		return fail(CodeSchemaInvalid, path)
	}
	seen := map[string]bool{}
	for _, c := range d.Categories {
		if c == "" || len(c) > MaxLabel || seen[c] {
			return fail(CodeSchemaInvalid, path+"/categories")
		}
		seen[c] = true
	}
	seen = map[string]bool{}
	for _, s := range d.Series {
		if !identifier(s.ID) || seen[s.ID] || s.Label == "" || len(s.Label) > MaxLabel || len(s.Unit) > MaxLabel || len(s.Values) != len(d.Categories) {
			return fail(CodeSchemaInvalid, path+"/series")
		}
		seen[s.ID] = true
		for _, v := range s.Values {
			if !finite(v) {
				return fail(CodeValueInvalid, path+"/series/values")
			}
		}
	}
	return nil
}

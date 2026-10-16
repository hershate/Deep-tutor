package surfacespec

import "encoding/json"

func (s *Spec) Redact(redact func(string) string) {
	s.Title = redact(s.Title)
	var walk func([]Node)
	walk = func(nodes []Node) {
		for i := range nodes {
			n := &nodes[i]
			n.Value = redact(n.Value)
			n.Label = redact(n.Label)
			if n.Dataset != nil {
				for j := range n.Dataset.Categories {
					n.Dataset.Categories[j] = redact(n.Dataset.Categories[j])
				}
				for j := range n.Dataset.Series {
					n.Dataset.Series[j].Label = redact(n.Dataset.Series[j].Label)
					n.Dataset.Series[j].Unit = redact(n.Dataset.Series[j].Unit)
				}
			}
			if n.Diff != nil {
				n.Diff.Before = redact(n.Diff.Before)
				n.Diff.After = redact(n.Diff.After)
			}
			if n.Kind == "kv" {
				n.Key = redact(n.Key)
			}
			if n.Field != nil {
				for j := range n.Field.Options {
					o := &n.Field.Options[j]
					o.Label = redact(o.Label)
					o.Description = redact(o.Description)
					o.Preview = redact(o.Preview)
					o.Group = redact(o.Group)
					o.Currency = redact(o.Currency)
					if o.Image != nil {
						o.Image.Alt = redact(o.Image.Alt)
						o.Image.Source = redact(o.Image.Source)
					}
					for k := range o.Facts {
						o.Facts[k].Label = redact(o.Facts[k].Label)
						o.Facts[k].Value = redact(o.Facts[k].Value)
					}
				}
				if n.Field.Type == "string" && len(n.Field.Default) != 0 {
					var value string
					if json.Unmarshal(n.Field.Default, &value) == nil {
						n.Field.Default, _ = json.Marshal(redact(value))
					}
				}
			}
			walk(n.Children)
		}
	}
	walk(s.Body)
}

package surfacespec

import "encoding/json"

func validateShape(raw []byte, s *Spec) error {
	var root map[string]json.RawMessage
	if err := json.Unmarshal(raw, &root); err != nil {
		return fail(CodeSchemaInvalid, "")
	}
	if err := allowedKeys(root, "spec_version title body"); err != nil {
		return err
	}
	var nodes []map[string]json.RawMessage
	if err := json.Unmarshal(root["body"], &nodes); err != nil {
		return fail(CodeSchemaInvalid, "/body")
	}
	return nodeShapes(nodes, s.Body)
}

func allowedKeys(raw map[string]json.RawMessage, allowed string) error {
	for key := range raw {
		if !containsToken(allowed, key) {
			return fail(CodeSchemaInvalid, "/"+key)
		}
	}
	return nil
}

func nodeShapes(raw []map[string]json.RawMessage, nodes []Node) error {
	for i, n := range nodes {
		allowed, err := nodeFields(n, "", map[string]*Field{})
		if err != nil {
			return err
		}
		if err := allowedKeys(raw[i], "kind "+allowed); err != nil {
			return err
		}
		if n.Diff != nil {
			var diff map[string]json.RawMessage
			if err := json.Unmarshal(raw[i]["diff"], &diff); err != nil || len(diff["before"]) == 0 || len(diff["after"]) == 0 {
				return fail(CodeSchemaInvalid, "/diff")
			}
			if err := allowedKeys(diff, "before after"); err != nil {
				return err
			}
		}
		if n.Field != nil {
			var f map[string]json.RawMessage
			if err := json.Unmarshal(raw[i]["field"], &f); err != nil {
				return fail(CodeSchemaInvalid, "/field")
			}
			fields := "type required default"
			switch n.Field.Type {
			case "number":
				fields += " min max"
			case "string":
				fields += " max_length"
			case "choice", "multichoice":
				fields += " options"
			}
			if err := allowedKeys(f, fields); err != nil {
				return err
			}
		}
		if len(n.Children) != 0 {
			var children []map[string]json.RawMessage
			if err := json.Unmarshal(raw[i]["children"], &children); err != nil {
				return fail(CodeSchemaInvalid, "/children")
			}
			if err := nodeShapes(children, n.Children); err != nil {
				return err
			}
		}
	}
	return nil
}

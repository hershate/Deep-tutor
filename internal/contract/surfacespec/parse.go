package surfacespec

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"strings"
)

func Parse(raw []byte) (*Spec, error) {
	if len(raw) > MaxSpecBytes {
		return nil, fail(CodeLimitExceeded, "")
	}
	if err := uniqueKeys(raw); err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var s Spec
	if err := dec.Decode(&s); err != nil {
		return nil, fail(CodeSchemaInvalid, "")
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, fail(CodeSchemaInvalid, "")
	}
	if err := s.Validate(); err != nil {
		return nil, err
	}
	if err := validateShape(raw, &s); err != nil {
		return nil, err
	}
	return &s, nil
}

func uniqueKeys(raw []byte) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	var keys []map[string]bool
	var nextKey []bool
	for {
		tok, err := dec.Token()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return fail(CodeSchemaInvalid, "")
		}
		if tok == nil {
			return fail(CodeSchemaInvalid, "")
		}
		if d, ok := tok.(json.Delim); ok {
			switch d {
			case '{':
				keys = append(keys, map[string]bool{})
				nextKey = append(nextKey, true)
			case '[':
				keys = append(keys, nil)
				nextKey = append(nextKey, false)
			default:
				if len(keys) == 0 {
					return fail(CodeSchemaInvalid, "")
				}
				keys = keys[:len(keys)-1]
				nextKey = nextKey[:len(nextKey)-1]
				if n := len(keys); n > 0 && keys[n-1] != nil {
					nextKey[n-1] = true
				}
			}
			if len(keys) > MaxDepth*3+4 {
				return fail(CodeLimitExceeded, "")
			}
			continue
		}
		n := len(keys)
		if n == 0 || keys[n-1] == nil {
			continue
		}
		if nextKey[n-1] {
			key, ok := tok.(string)
			if !ok {
				return fail(CodeSchemaInvalid, "")
			}
			key = strings.ToLower(key)
			if keys[n-1][key] {
				return fail(CodeSchemaInvalid, "")
			}
			keys[n-1][key] = true
		}
		nextKey[n-1] = !nextKey[n-1]
	}
}

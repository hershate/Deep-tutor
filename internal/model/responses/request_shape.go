package responses

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// requestShape describes the request body that was refused without its content:
// total size, the input items by kind, and the largest one with its place in
// the list. A gateway that answers "at line 1 column N" or "no variant matched"
// names a position or a type, and this is what the host can say about both.
func requestShape(body map[string]any, payloadBytes int) string {
	items, ok := body["input"].([]map[string]any)
	if !ok {
		return ""
	}
	kinds := map[string]int{}
	largest, largestBytes := -1, 0
	for i, item := range items {
		kind := "message"
		if t, ok := item["type"].(string); ok {
			kind = t
		}
		kinds[kind]++
		if b, err := json.Marshal(item); err == nil && len(b) > largestBytes {
			largest, largestBytes = i, len(b)
		}
	}
	names := make([]string, 0, len(kinds))
	for k := range kinds {
		names = append(names, k)
	}
	sort.Strings(names)
	parts := make([]string, 0, len(names))
	for _, k := range names {
		parts = append(parts, fmt.Sprintf("%s %d", k, kinds[k]))
	}
	shape := fmt.Sprintf("Request shape: %d bytes, input has %d items (%s)", payloadBytes, len(items), strings.Join(parts, ", "))
	if largest >= 0 {
		kind := "message"
		if t, ok := items[largest]["type"].(string); ok {
			kind = t
		}
		shape += fmt.Sprintf("; largest is input[%d], a %s of %d bytes", largest, kind, largestBytes)
	}
	return shape + "."
}

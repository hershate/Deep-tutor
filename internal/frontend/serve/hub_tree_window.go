package serve

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
)

// defaultTreeWindow is how many conversations of a workspace /tree builds when
// the request does not ask for more.
const defaultTreeWindow = 50

// treeWindow says how much of each workspace /tree builds. A window is a
// request for the newest N rows; the counts beside them always cover all rows.
type treeWindow struct {
	limit  int
	limits map[string]int
	// scope picks which rows the window counts: every row, the active (not
	// archived) ones, or only the archived. A pinned active conversation is
	// always built, whatever the scope, so the pinned count never moves with it.
	scope treeScope
	// query keeps the rows whose title or name contains it, case-blind; a
	// workspace whose own name contains it keeps all of its rows.
	query string
}

type treeScope string

const (
	scopeEvery    treeScope = ""
	scopeActive   treeScope = "active"
	scopeArchived treeScope = "archived"
)

// maxTreeWindow is the most rows one workspace's window builds; a larger
// request is served at this size, and Remaining says what is left. 0 stays
// "every row" for callers that ask for the whole list.
const maxTreeWindow = 300

// maxTreeQuery bounds the search text the kernel will match against.
const maxTreeQuery = 200

// limitFor is the row bound for one workspace; 0 means every row.
func (w treeWindow) limitFor(root string) int {
	if n, ok := w.limits[root]; ok {
		return n
	}
	return w.limit
}

// parseTreeWindow reads `limit` (every workspace's bound) and `limits` (a JSON
// object of per-workspace bounds); 0 means unbounded. It answers the request
// itself when a value is unreadable.
func parseTreeWindow(w http.ResponseWriter, r *http.Request) (treeWindow, bool) {
	window := treeWindow{limit: defaultTreeWindow}
	q := r.URL.Query()
	if raw := q.Get("limit"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 0 {
			badValue(w, "limit", "a non-negative integer")
			return window, false
		}
		window.limit = min(n, maxTreeWindow)
	}
	if raw := q.Get("limits"); raw != "" {
		if err := json.Unmarshal([]byte(raw), &window.limits); err != nil {
			badValue(w, "limits", "a JSON object of workspace to non-negative integer")
			return window, false
		}
		for _, n := range window.limits {
			if n < 0 {
				badValue(w, "limits", "a JSON object of workspace to non-negative integer")
				return window, false
			}
		}
		for root, n := range window.limits {
			window.limits[root] = min(n, maxTreeWindow)
		}
	}
	switch scope := treeScope(q.Get("scope")); scope {
	case scopeEvery, scopeActive, scopeArchived:
		window.scope = scope
	default:
		badValue(w, "scope", "active or archived")
		return window, false
	}
	if raw := strings.TrimSpace(q.Get("q")); raw != "" {
		if len(raw) > maxTreeQuery {
			badValue(w, "q", "at most 200 bytes")
			return window, false
		}
		window.query = strings.ToLower(raw)
	}
	return window, true
}

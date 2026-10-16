package tutor

import (
	"strings"
	"unicode"
	"unicode/utf8"
)

// search.go — the tutor's global search, Deep-tutor's /search endpoint:
// one substring query answered across plans, knowledge nodes, notes, and
// KB documents. Pure file reads, no LLM, nothing written.

// SearchItem is one hit; which fields carry payload depends on the domain.
type SearchItem struct {
	ID        string `json:"id"`
	PlanID    string `json:"plan_id,omitempty"`
	Name      string `json:"name,omitempty"`
	Topic     string `json:"topic,omitempty"`
	Title     string `json:"title,omitempty"`
	Snippet   string `json:"snippet,omitempty"`
	PlanTopic string `json:"plan_topic,omitempty"`
}

// SearchDomain is one entity family's results: the true total plus the
// first `limit` items.
type SearchDomain struct {
	Total int          `json:"total"`
	Items []SearchItem `json:"items"`
}

// SearchResults groups every domain the query touched.
type SearchResults struct {
	Query  string       `json:"q"`
	Plans  SearchDomain `json:"plans"`
	Nodes  SearchDomain `json:"nodes"`
	Notes  SearchDomain `json:"notes"`
	KbDocs SearchDomain `json:"kb_docs"`
}

// searchLimitCeil caps ?limit=; Deep-tutor serves 10 by default.
const searchLimitCeil = 50

// snippetAround centers a window on the first match so long note bodies
// show why they hit.
func snippetAround(text, q string) string {
	runes := []rune(strings.ReplaceAll(text, "\n", " "))
	if len(runes) > 120 {
		runes = runes[:120]
	}
	lower := strings.ToLower(string(runes))
	at := strings.Index(lower, strings.ToLower(q))
	if at < 0 {
		if len(runes) > 60 {
			return string(runes[:60]) + "…"
		}
		return string(runes)
	}
	start := max(at-24, 0)
	end := min(at+utf8.RuneCountInString(q)+36, len(runes))
	out := string(runes[start:end])
	if start > 0 {
		out = "…" + out
	}
	if end < len(runes) {
		out += "…"
	}
	return out
}

// containsFold answers whether hay contains the already-lowercased needle
// case-insensitively, without copying hay: search walks every node name and
// note body, and ToLower-per-call dominated its allocation profile. The
// store's JSON is valid UTF-8, so the rune-wise fold matches strings.ToLower
// exactly on this input.
func containsFold(hay, lowNeedle string) bool {
	if lowNeedle == "" {
		return true
	}
	first, _ := utf8.DecodeRuneInString(lowNeedle)
	for i := 0; i < len(hay); {
		r, size := utf8.DecodeRuneInString(hay[i:])
		if unicode.ToLower(r) == first && foldMatchAt(hay[i:], lowNeedle) {
			return true
		}
		i += size
	}
	return false
}

// foldMatchAt compares s's leading runes against the lowercased needle.
func foldMatchAt(s, lowNeedle string) bool {
	for _, want := range lowNeedle {
		r, size := utf8.DecodeRuneInString(s)
		if size == 0 || unicode.ToLower(r) != want {
			return false
		}
		s = s[size:]
	}
	return true
}

// SearchAll runs one substring query across plans (topic/title), knowledge
// nodes (name, tagged with their plan's topic), notes (title/body with
// a snippet), and KB documents (titles everywhere, content in the newest-50
// parsed scan with snippets).
func SearchAll(store *Store, query string, limit int) (SearchResults, error) {
	query = strings.TrimSpace(query)
	if limit <= 0 {
		limit = 10
	}
	if limit > searchLimitCeil {
		limit = searchLimitCeil
	}
	res := SearchResults{Query: query,
		Plans:  SearchDomain{Items: []SearchItem{}},
		Nodes:  SearchDomain{Items: []SearchItem{}},
		Notes:  SearchDomain{Items: []SearchItem{}},
		KbDocs: SearchDomain{Items: []SearchItem{}}}
	if query == "" {
		return res, nil
	}
	q := strings.ToLower(query)

	plans, err := store.Plans()
	if err != nil {
		return res, err
	}
	for _, p := range plans {
		if !containsFold(p.Topic, q) && !containsFold(p.Title, q) {
			continue
		}
		res.Plans.Total++
		if len(res.Plans.Items) < limit {
			res.Plans.Items = append(res.Plans.Items, SearchItem{
				ID: p.ID, Topic: p.Topic, Title: p.Title,
			})
		}
	}

	// Nodes: every plan's knowledge map, each hit tagged with its plan.
	nodeItems, nodeTotal := scanNodes(store, plans, q, limit)
	res.Nodes.Total = nodeTotal
	for _, it := range nodeItems {
		if len(res.Nodes.Items) >= limit {
			break
		}
		res.Nodes.Items = append(res.Nodes.Items, it)
	}

	notes, err := store.Notes()
	if err != nil {
		return res, err
	}
	for _, n := range notes {
		hitTitle := containsFold(n.Title, q)
		hitBody := containsFold(n.Body, q)
		if !hitTitle && !hitBody {
			continue
		}
		res.Notes.Total++
		if len(res.Notes.Items) < limit {
			item := SearchItem{ID: n.ID, Title: n.Title}
			if hitBody {
				item.Snippet = snippetAround(n.Body, query)
			}
			res.Notes.Items = append(res.Notes.Items, item)
		}
	}

	// KB documents: title hits across the whole library, then bounded
	// content hits (newest 50 parsed) with snippets — Deep-tutor's kb list
	// search, same honest no-index boundary.
	kbDocs, err := store.KbDocs()
	if err != nil {
		return res, err
	}
	titleHit := map[string]bool{}
	for i := range kbDocs {
		d := &kbDocs[i]
		if !containsFold(d.Title, q) {
			continue
		}
		titleHit[d.ID] = true
		res.KbDocs.Total++
		if len(res.KbDocs.Items) < limit {
			res.KbDocs.Items = append(res.KbDocs.Items, SearchItem{ID: d.ID, Title: d.Title})
		}
	}
	if room := limit - len(res.KbDocs.Items); room > 0 {
		hitDocs, snippets := store.KbContentSearch(query, room, titleHit, "")
		for i := range hitDocs {
			res.KbDocs.Total++
			if len(res.KbDocs.Items) < limit {
				res.KbDocs.Items = append(res.KbDocs.Items, SearchItem{
					ID: hitDocs[i].ID, Title: hitDocs[i].Title, Snippet: snippets[i],
				})
			}
		}
	}
	return res, nil
}

// scanNodes walks every plan's node names for the needle — plans in
// parallel, hits merged in list order so the result set is deterministic.
// Only the node list is decoded; the rest of a plan never enters memory.
func scanNodes(store *Store, plans []PlanSummary, q string, limit int) ([]SearchItem, int) {
	type planHits struct {
		items []SearchItem
		total int
	}
	hits := make([]planHits, len(plans))
	parallelFor(len(plans), func(i int) {
		nodes, err := store.planNodeNames(plans[i].ID)
		if err != nil {
			return
		}
		p := plans[i]
		for _, n := range nodes {
			if !containsFold(n.Name, q) {
				continue
			}
			hits[i].total++
			if len(hits[i].items) < limit {
				hits[i].items = append(hits[i].items, SearchItem{
					ID: n.SID, PlanID: p.ID, Name: n.Name, PlanTopic: p.Topic,
				})
			}
		}
	})
	var items []SearchItem
	total := 0
	for i := range plans {
		total += hits[i].total
		items = append(items, hits[i].items...)
	}
	return items, total
}

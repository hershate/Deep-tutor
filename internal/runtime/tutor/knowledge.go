package tutor

// MaxKnowledgeNodes caps the knowledge map; Deep-tutor truncates at 100.
const MaxKnowledgeNodes = 100

// CleanKnowledgeMap repairs a model-produced map in place: duplicate node ids
// keep the first, difficulty clamps to 1-5, dangling edges drop, edges that
// would close a cycle drop so the graph stays a DAG, over-long node lists
// truncate (with dangling parents cleared), and dangling parent ids clear.
func CleanKnowledgeMap(m *KnowledgeMap) {
	seen := make(map[string]bool, len(m.Nodes))
	nodes := m.Nodes[:0]
	for _, n := range m.Nodes {
		if n.SID != "" && seen[n.SID] {
			continue
		}
		if n.SID != "" {
			seen[n.SID] = true
		}
		nodes = append(nodes, n)
	}
	for i := range nodes {
		nodes[i].Difficulty = max(1, min(5, nodes[i].Difficulty))
	}

	valid := make(map[string]bool, len(nodes))
	for _, n := range nodes {
		if n.SID != "" {
			valid[n.SID] = true
		}
	}
	edges := m.Edges[:0]
	for _, e := range m.Edges {
		if valid[e.From] && valid[e.To] {
			edges = append(edges, e)
		}
	}
	edges = breakCycles(edges)

	m.Note = ""
	if len(nodes) > MaxKnowledgeNodes {
		nodes = nodes[:MaxKnowledgeNodes]
		valid = make(map[string]bool, len(nodes))
		for _, n := range nodes {
			if n.SID != "" {
				valid[n.SID] = true
			}
		}
		kept := edges[:0]
		for _, e := range edges {
			if valid[e.From] && valid[e.To] {
				kept = append(kept, e)
			}
		}
		edges = kept
		m.Note = "节点数超上限，已截断"
	}
	for i := range nodes {
		if nodes[i].ParentID != "" && !valid[nodes[i].ParentID] {
			nodes[i].ParentID = ""
		}
	}
	m.Nodes = nodes
	m.Edges = edges
}

// breakCycles keeps edges in order, dropping any whose reverse endpoint is
// already reachable through kept edges.
func breakCycles(edges []KnowledgeEdge) []KnowledgeEdge {
	adj := make(map[string][]string, len(edges))
	kept := make([]KnowledgeEdge, 0, len(edges))
	for _, e := range edges {
		if hasPath(adj, e.To, e.From) {
			continue
		}
		adj[e.From] = append(adj[e.From], e.To)
		kept = append(kept, e)
	}
	return kept
}

func hasPath(adj map[string][]string, from, to string) bool {
	if from == to {
		return true
	}
	visited := map[string]bool{from: true}
	stack := []string{from}
	for len(stack) > 0 {
		node := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		for _, next := range adj[node] {
			if next == to {
				return true
			}
			if !visited[next] {
				visited[next] = true
				stack = append(stack, next)
			}
		}
	}
	return false
}

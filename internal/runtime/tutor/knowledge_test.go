package tutor

import (
	"strconv"
	"testing"
)

func TestCleanKnowledgeMapDedupesAndClamps(t *testing.T) {
	m := &KnowledgeMap{
		Nodes: []KnowledgeNode{
			{SID: "k1", Name: "basics", Difficulty: 99},
			{SID: "k1", Name: "duplicate", Difficulty: 2},
			{SID: "k2", Name: "core", Difficulty: 0},
		},
		Edges: []KnowledgeEdge{{From: "k1", To: "k2"}},
	}
	CleanKnowledgeMap(m)
	if len(m.Nodes) != 2 {
		t.Fatalf("duplicate ids keep the first: got %d nodes", len(m.Nodes))
	}
	if m.Nodes[0].Name != "basics" || m.Nodes[0].Difficulty != 5 || m.Nodes[1].Difficulty != 1 {
		t.Fatalf("difficulty clamps into 1-5: %+v", m.Nodes)
	}
}

func TestCleanKnowledgeMapDropsDanglingEdges(t *testing.T) {
	m := &KnowledgeMap{
		Nodes: []KnowledgeNode{{SID: "k1"}, {SID: "k2"}},
		Edges: []KnowledgeEdge{
			{From: "k1", To: "k2"},
			{From: "k1", To: "ghost"},
			{From: "ghost", To: "k2"},
		},
	}
	CleanKnowledgeMap(m)
	if len(m.Edges) != 1 || m.Edges[0].To != "k2" {
		t.Fatalf("dangling edges drop: %+v", m.Edges)
	}
}

func TestCleanKnowledgeMapBreaksCycles(t *testing.T) {
	m := &KnowledgeMap{
		Nodes: []KnowledgeNode{{SID: "a"}, {SID: "b"}, {SID: "c"}},
		Edges: []KnowledgeEdge{
			{From: "a", To: "b"},
			{From: "b", To: "c"},
			{From: "c", To: "a"}, // closes the cycle
			{From: "c", To: "b"}, // would close a 2-cycle with b->c
		},
	}
	CleanKnowledgeMap(m)
	if len(m.Edges) != 2 {
		t.Fatalf("cycle-closing edges drop, earlier edges keep: %+v", m.Edges)
	}
	for _, e := range m.Edges {
		if e.From == "c" {
			t.Fatalf("the first two edges survive, not the cycle closers: %+v", m.Edges)
		}
	}
}

func TestCleanKnowledgeMapSelfEdgeDrops(t *testing.T) {
	m := &KnowledgeMap{
		Nodes: []KnowledgeNode{{SID: "a"}},
		Edges: []KnowledgeEdge{{From: "a", To: "a"}},
	}
	CleanKnowledgeMap(m)
	if len(m.Edges) != 0 {
		t.Fatalf("self edge is a cycle: %+v", m.Edges)
	}
}

func TestCleanKnowledgeMapTruncatesAndClearsParents(t *testing.T) {
	nodes := make([]KnowledgeNode, MaxKnowledgeNodes+5)
	for i := range nodes {
		nodes[i] = KnowledgeNode{SID: "k" + strconv.Itoa(i), Difficulty: 1}
	}
	nodes[MaxKnowledgeNodes].Name = "beyond" // parent referenced by a kept node
	m := &KnowledgeMap{
		Nodes: nodes,
		Edges: []KnowledgeEdge{
			{From: "k1", To: "k0"},
			{From: "k0", To: nodes[MaxKnowledgeNodes].SID},
		},
	}
	m.Nodes[0].ParentID = nodes[MaxKnowledgeNodes].SID
	CleanKnowledgeMap(m)
	if len(m.Nodes) != MaxKnowledgeNodes {
		t.Fatalf("nodes truncate at %d: got %d", MaxKnowledgeNodes, len(m.Nodes))
	}
	if m.Note == "" {
		t.Fatalf("truncation leaves a note")
	}
	if m.Nodes[0].ParentID != "" {
		t.Fatalf("dangling parent clears after truncation: %q", m.Nodes[0].ParentID)
	}
	for _, e := range m.Edges {
		if e.To == nodes[MaxKnowledgeNodes].SID {
			t.Fatalf("edges to truncated nodes drop: %+v", m.Edges)
		}
	}
}

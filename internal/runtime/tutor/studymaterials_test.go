package tutor

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func node(id, name string, difficulty int) KnowledgeNode {
	return KnowledgeNode{SID: id, Name: name, Difficulty: difficulty, MustLearn: true}
}

func TestGroupNodesIntoUnits(t *testing.T) {
	stages := []Stage{
		{Index: 0, Name: "基础", Difficulty: 1, Topics: []StageTopic{{Name: "变量"}, {Name: "循环"}}},
		{Index: 1, Name: "进阶", Difficulty: 4, Topics: []StageTopic{{Name: "并发"}}},
	}
	// Two easy nodes, one hard node standing alone, two easy nodes again.
	nodes := []KnowledgeNode{
		node("n1", "变量", 1), node("n2", "循环", 2),
		node("n3", "并发", 5),
		node("n4", "锁", 4), node("n5", "通道", 2),
	}
	units := GroupNodesIntoUnits(nodes, stages)
	if len(units) < 3 {
		t.Fatalf("units = %d (%+v)", len(units), units)
	}
	// The hard node stands alone.
	for _, u := range units {
		for _, n := range u.Nodes {
			if n.SID == "n3" && len(u.Nodes) != 1 {
				t.Fatalf("hard node clustered: %+v", u)
			}
		}
	}
	// No unit carries more than the node cap.
	for _, u := range units {
		if len(u.Nodes) > unitMaxNodes {
			t.Fatalf("unit over cap: %+v", u)
		}
	}
	if GroupNodesIntoUnits(nodes[:2], stages) != nil {
		t.Fatal("fewer than three nodes must fall back to stage units")
	}
}

func TestSanitizeCitations(t *testing.T) {
	pool := map[string]bool{normalizePoolURL("https://good.dev/learn"): true}
	content := strings.Join([]string{
		"### 概述",
		"看 [官方文档](https://good.dev/learn) 与 [可疑站点](https://evil.dev/x)。",
		"- [ ] 引用 [坏链接](https://evil.dev/y) 的列表项应整行丢弃",
		"- [ ] 好链接 [教程](https://good.dev/learn) 的列表项保留",
		"autolink <https://evil.dev/z> 应消失，<https://good.dev/learn> 保留。",
		"裸链 https://evil.dev/raw 变行内代码，https://good.dev/learn 保留。",
		"[def]: https://evil.dev/def",
		"行内代码 `https://evil.dev/code` 原样，代码块下一行也原样：",
		"```",
		"https://evil.dev/fenced 不处理",
		"```",
	}, "\n")
	out := SanitizeCitations(content, pool)
	if strings.Contains(out, "evil.dev/x") && strings.Contains(out, "https://evil.dev/x") {
		t.Fatalf("foreign standard link still clickable: %s", out)
	}
	if !strings.Contains(out, "[可疑站点](https://evil.dev/x)") && strings.Contains(out, "https://evil.dev/x") {
		// acceptable: either the link became bare text or the url vanished;
		// it must not remain a markdown link
		if strings.Contains(out, "](https://evil.dev/x)") {
			t.Fatalf("foreign markdown link survived: %s", out)
		}
	}
	if !strings.Contains(out, "[官方文档](https://good.dev/learn)") {
		t.Fatalf("whitelisted link stripped: %s", out)
	}
	if strings.Contains(out, "坏链接") {
		t.Fatalf("all-foreign list item kept: %s", out)
	}
	if !strings.Contains(out, "教程](https://good.dev/learn)") {
		t.Fatalf("list item with good link dropped: %s", out)
	}
	if strings.Contains(out, "<https://evil.dev/z>") {
		t.Fatalf("foreign autolink kept: %s", out)
	}
	if !strings.Contains(out, "<https://good.dev/learn>") {
		t.Fatalf("whitelisted autolink dropped: %s", out)
	}
	if !strings.Contains(out, "`https://evil.dev/raw`") {
		t.Fatalf("bare foreign url not neutralized: %s", out)
	}
	if !strings.Contains(out, "https://good.dev/learn 保留") {
		t.Fatalf("whitelisted bare url mangled: %s", out)
	}
	if strings.Contains(out, "evil.dev/def") {
		t.Fatalf("foreign link definition kept: %s", out)
	}
	if !strings.Contains(out, "`https://evil.dev/code`") {
		t.Fatalf("inline code touched: %s", out)
	}
	if !strings.Contains(out, "https://evil.dev/fenced 不处理") {
		t.Fatalf("fenced code touched: %s", out)
	}
}

func unitReply(content string) string {
	return `{"objectives":["学完后能够解释基础概念"],"prerequisites":["无"],"estimated_time":"45 min","content_md":` + jsonString(content) + `}`
}

func jsonString(s string) string {
	var sb strings.Builder
	sb.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			sb.WriteString("\\\"")
		case '\\':
			sb.WriteString("\\\\")
		case '\n':
			sb.WriteString("\\n")
		default:
			sb.WriteRune(r)
		}
	}
	sb.WriteByte('"')
	return sb.String()
}

// materialsService builds a service with a plan-shaped fixture.
func materialsService(t *testing.T, reply func(system string, call int) string) (*Service, *Plan) {
	t.Helper()
	store := openTestStore(t)
	now := time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)
	svc := NewService(store, ServiceOptions{
		Provider: func() (LLM, error) { return &fakeLLM{t: t, reply: reply}, nil },
		Searcher: fakeSearcher{},
		Now:      func() time.Time { return now },
	})
	sess, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	plan := &Plan{
		ID: "plan1", SessionID: sess.ID, Version: 1, Title: "Go 入门", Topic: "Go",
		Knowledge: KnowledgeMap{Nodes: []KnowledgeNode{
			node("k1", "变量与类型", 2), node("k2", "流程控制", 2), node("k3", "函数", 3),
		}},
		Stages: []Stage{{
			Index: 0, Name: "基础", Goal: "打地基", Difficulty: 2,
			Topics:    []StageTopic{{Name: "变量与类型"}, {Name: "流程控制"}, {Name: "函数"}},
			Resources: []Resource{{StageIndex: 0, Title: "官方教程", URL: "https://go.dev/learn", Verified: VerifyOK}},
		}},
		Schedule: []ScheduleDay{{Date: "2026-10-15", StageIndex: 0, Tasks: []string{"读文档"}, EstMinutes: 45}},
	}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	return svc, plan
}

func TestGenerateMaterialsStreamsUnits(t *testing.T) {
	svc, plan := materialsService(t, func(system string, call int) string {
		if strings.Contains(system, "UnitContentSpec") {
			return unitReply("### 概述\n\n这是单元内容。参考 [官方](https://go.dev/learn) 与 [杜撰](https://made-up.dev/x)。")
		}
		return intentJSON()
	})
	var events []MaterialsEvent
	snapshot, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{}, func(ev MaterialsEvent) {
		events = append(events, ev)
	})
	if err != nil {
		t.Fatalf("GenerateMaterials: %v", err)
	}
	// 3 nodes ≥ fallback floor → node-driven units.
	if snapshot.UnitCount == 0 {
		t.Fatalf("no units built")
	}
	if len(events) < snapshot.UnitCount*2+1 {
		t.Fatalf("events = %d for %d units", len(events), snapshot.UnitCount)
	}
	if events[0].Type != "unit_start" || events[len(events)-1].Type != "done" {
		t.Fatalf("event sequence = %+v", events)
	}
	for _, u := range snapshot.Units {
		if !strings.Contains(u.ContentMD, "官方](https://go.dev/learn)") {
			t.Fatalf("whitelisted citation stripped: %q", u.ContentMD)
		}
		if strings.Contains(u.ContentMD, "](https://made-up.dev/x)") {
			t.Fatalf("fabricated citation survived: %q", u.ContentMD)
		}
	}
	// Idempotent: persisted units render back without new model calls.
	again, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{}, nil)
	if err != nil {
		t.Fatalf("idempotent render: %v", err)
	}
	if again.Markdown != snapshot.Markdown || again.UnitCount != snapshot.UnitCount {
		t.Fatalf("idempotent render drifted")
	}
	if !strings.Contains(snapshot.IndexMD, "自学路线图") || !strings.Contains(snapshot.IndexMD, "变量与类型") {
		t.Fatalf("index = %q", snapshot.IndexMD)
	}
}

func TestGenerateMaterialsUnitRegeneration(t *testing.T) {
	svc, plan := materialsService(t, func(system string, call int) string {
		if strings.Contains(system, "UnitContentSpec") {
			return unitReply("第一版内容")
		}
		return intentJSON()
	})
	first, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	target := first.Units[0]
	calls := 0
	svc.opt.Provider = func() (LLM, error) {
		return &fakeLLM{t: t, reply: func(system string, call int) string {
			if strings.Contains(system, "UnitContentSpec") {
				calls++
				return unitReply("重生成后的内容")
			}
			return intentJSON()
		}}, nil
	}
	second, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{UnitIDs: []string{target.ID}}, nil)
	if err != nil {
		t.Fatalf("regenerate: %v", err)
	}
	if calls != 1 {
		t.Fatalf("regenerated %d units, want 1", calls)
	}
	if !strings.Contains(second.Units[0].ContentMD, "重生成后的内容") {
		t.Fatalf("unit not replaced: %q", second.Units[0].ContentMD)
	}
	if second.UnitCount != first.UnitCount {
		t.Fatalf("unit count drifted: %d vs %d", second.UnitCount, first.UnitCount)
	}
}

func TestGenerateMaterialsDegradesWithoutModel(t *testing.T) {
	svc, plan := materialsService(t, func(system string, call int) string { return intentJSON() })
	svc.opt.Provider = func() (LLM, error) { return nil, ErrNoLLM }
	snapshot, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{}, nil)
	if err != nil {
		t.Fatalf("GenerateMaterials: %v", err)
	}
	if snapshot.UnitCount == 0 {
		t.Fatal("no units")
	}
	for _, u := range snapshot.Units {
		if !u.Degraded {
			t.Fatalf("unit not marked degraded: %+v", u)
		}
		if !strings.Contains(u.ContentMD, "降级为占位大纲") {
			t.Fatalf("brief outline missing: %q", u.ContentMD)
		}
	}
}

func TestGenerateMaterialsBudgetGuard(t *testing.T) {
	svc, plan := materialsService(t, func(system string, call int) string { return intentJSON() })
	svc.opt.DailyBudget = 1.0
	if err := svc.store.AddUsage("2026-10-14", 2.0); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{}, nil); !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("err = %v, want ErrBudgetExceeded", err)
	}
}

func TestGenerateMaterialsBadScope(t *testing.T) {
	svc, plan := materialsService(t, func(system string, call int) string { return intentJSON() })
	if _, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{UnitIDs: []string{"missing"}}, nil); err == nil {
		t.Fatal("unknown unit id accepted")
	}
	if _, err := svc.GenerateMaterials(context.Background(), plan.ID, MaterialsRequest{
		UnitIDs: []string{"a"}, StageIndexes: []int{0},
	}, nil); err == nil {
		t.Fatal("unit_ids + scope accepted")
	}
}

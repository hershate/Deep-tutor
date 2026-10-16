package tutor

import (
	"context"
	"encoding/json"
	"regexp"
	"strings"

	"reasonix/internal/contract/provider"
	"reasonix/internal/tools/websearch"
)

// searchscreen.go is the mandatory relevance gate: every citation a plan
// carries has passed one LLM judgment of whether it teaches the stage's
// topic at the learner's level. Failures drop candidates — 宁缺勿滥.

const screenPrompt = `你是 Reasonix Tutor 的「资源内容适配性筛选」工具。候选资源已通过可达性校验（HTTP 200），你的任务：判定每个候选与对应学习阶段主题、学习者水平的**内容强相关性**与适配性。

输入（user message JSON）：{"topic":"学习主题","level":"学习者水平","stages":["阶段名",...],"candidates":[{"stage_index":0,"title":"...","url":"...","snippet":"正文摘要（可能为空）"}]}

仅返回 JSON 数组（不要 markdown 代码块、不要解释），每候选一项：
[{"url":"候选 url 原文","suitable":true,"label":"ok|uncertain","note":"一句话说明"}]

规则：
- 优先依据 snippet 正文摘要判断内容是否真正讲该主题；snippet 为空时再退回标题与常识。
- suitable=false：内容与该阶段主题不相关或仅擦边（如主题讲 Python 基础，候选是 Java 框架/导览页/无关资讯）——该候选将被丢弃。只有内容确实覆盖该主题才 true，宁缺勿滥。
- label="ok"：内容相关且难度/深度适配该阶段（初级阶段配入门教程）。
- label="uncertain"：相关但难度/质量不确定（明显过深、内容陈旧、清单水文、snippet 不足以判定等）。
- note：中文一句话（≤40 字），uncertain 时说明原因；ok 时可留空。
- 必须覆盖 candidates 中的每个 url，url 原样返回（不改写、不编造）。
- 拿不准一律 uncertain，不臆断 ok；不得因「标题含关键词」就判相关，须正文佐证。

仅返回符合上述格式的 JSON 数组。`

const unitScreenPrompt = `你是 Reasonix Tutor 的「自学单元补充资料筛选」工具。候选已通过可达性校验（HTTP 200）。判定每个候选与自学单元主题的**内容强相关性**。

输入（user message JSON）：{"topic":"单元主题（知识点/单元标题）","candidates":[{"title":"...","url":"...","snippet":"正文摘要（可能为空）"}]}

仅返回 JSON 数组（不要 markdown 代码块、不要解释），每候选一项：
[{"url":"候选 url 原文","suitable":true,"label":"ok|uncertain","note":"一句话说明"}]

规则：
- 优先依据 snippet 判断是否真正讲该单元主题；snippet 为空时退回标题与常识。
- suitable=false：内容与单元主题不相关或仅擦边（导览页/无关资讯/同名不同义）——该候选将被丢弃。只有确实覆盖该主题才 true，宁缺勿滥。
- label="ok"：相关且适合自学该单元；label="uncertain"：相关但难度/质量不确定或 snippet 不足判定。
- note：中文一句话（≤40 字），uncertain 时说明原因；ok 时可留空。
- 必须覆盖每个候选 url，url 原样返回（不改写、不编造）；拿不准一律 uncertain。

仅返回符合上述格式的 JSON 数组。`

// screenVerdict is the gate's judgment of one URL.
type screenVerdict struct {
	Suitable bool
	Label    VerifyLabel
	Note     string
}

// coerceSuitable reads a model's boolean through the usual sloppy spellings;
// an unreadable value stays uncovered and the candidate is dropped.
func coerceSuitable(v any) (bool, bool) {
	switch t := v.(type) {
	case bool:
		return t, true
	case float64:
		if t == 1 {
			return true, true
		}
		if t == 0 {
			return false, true
		}
	case string:
		s := strings.ToLower(strings.TrimRight(strings.TrimSpace(t), ",.，。"))
		switch s {
		case "true", "yes", "1", "ok", "y", "t":
			return true, true
		case "false", "no", "0", "n", "f":
			return false, true
		}
	}
	return false, false
}

var screenURLShape = regexp.MustCompile(`(?i)^([a-z][a-z0-9+.\-]*://)([^/]+)(.*)$`)

// screenURLKey normalizes a URL for matching the gate's reply against the
// candidate list: fragment dropped, trailing slash dropped, scheme and host
// lower-cased — the exact rewrites models make.
func screenURLKey(raw string) string {
	u := strings.TrimSpace(raw)
	if u == "" {
		return ""
	}
	if frag := strings.Index(u, "#"); frag >= 0 {
		u = u[:frag]
	}
	u = strings.TrimRight(u, "/")
	if m := screenURLShape.FindStringSubmatch(u); m != nil {
		return strings.ToLower(m[1]) + strings.ToLower(m[2]) + m[3]
	}
	return u
}

// parseScreenVerdicts reads the gate's JSON array; nil means unparseable and
// the caller drops everything.
func parseScreenVerdicts(text string) map[string]screenVerdict {
	s := strings.TrimSpace(text)
	if fenced, ok := strings.CutPrefix(s, "```"); ok {
		s = strings.Trim(fenced, "`")
		s = strings.TrimSpace(strings.TrimPrefix(s, "json"))
	}
	start := strings.Index(s, "[")
	end := strings.LastIndex(s, "]")
	if start < 0 || end <= start {
		return nil
	}
	var rows []map[string]any
	if err := json.Unmarshal([]byte(s[start:end+1]), &rows); err != nil {
		return nil
	}
	verdicts := map[string]screenVerdict{}
	for _, row := range rows {
		rawURL, _ := row["url"].(string)
		if rawURL == "" {
			continue
		}
		suitable, ok := coerceSuitable(row["suitable"])
		if !ok {
			continue
		}
		label := VerifyUncertain
		if l, _ := row["label"].(string); l == "ok" {
			label = VerifyOK
		}
		note, _ := row["note"].(string)
		verdicts[rawURL] = screenVerdict{Suitable: suitable, Label: label, Note: note}
	}
	return verdicts
}

// applyVerdicts keeps the candidates the gate explicitly approved; labels
// only downgrade, never upgrade.
func applyVerdicts(resources []Resource, verdicts map[string]screenVerdict) []Resource {
	normalized := map[string]screenVerdict{}
	for raw, verdict := range verdicts {
		key := screenURLKey(raw)
		if key == "" {
			continue
		}
		if _, exists := normalized[key]; !exists {
			normalized[key] = verdict
		}
	}
	var kept []Resource
	for _, r := range resources {
		verdict, covered := normalized[screenURLKey(r.URL)]
		if !covered || !verdict.Suitable {
			continue
		}
		if verdict.Label == VerifyUncertain {
			r.Verified = VerifyUncertain
		}
		if verdict.Note != "" {
			note := []rune(verdict.Note)
			if len(note) > 200 {
				note = note[:200]
			}
			r.Note = string(note)
		}
		kept = append(kept, r)
	}
	return kept
}

// screen is the plan-level gate: one batched judgment over the stage-tagged
// candidates, two attempts, everything dropped on failure.
func (w *WebSearcher) screen(ctx context.Context, llm LLM, q ResourceQuery, resources []Resource) ([]Resource, string, Usage) {
	payload := struct {
		Topic      string            `json:"topic"`
		Level      string            `json:"level"`
		Stages     []string          `json:"stages"`
		Candidates []screenCandidate `json:"candidates"`
	}{
		Topic: q.Topic,
		Level: q.Level,
	}
	for _, st := range q.Stages {
		payload.Stages = append(payload.Stages, st.Name)
	}
	for _, r := range resources {
		payload.Candidates = append(payload.Candidates, screenCandidate{
			StageIndex: r.StageIndex,
			Title:      truncateRunes(r.Title, 80),
			URL:        r.URL,
			Snippet:    truncateRunes(r.Snippet, 400),
		})
	}
	verdicts, usage := w.screenRound(ctx, llm, screenPrompt, payload, len(payload.Candidates))
	if verdicts == nil {
		return nil, "AI 相关性筛选失败（已重试），为避免引入未经验证的内容，已过滤全部候选资源", usage
	}
	return applyVerdicts(resources, verdicts), "", usage
}

// screenCandidate is one gate input row; snippet feeds content-level
// judgment instead of title-only guessing.
type screenCandidate struct {
	StageIndex int    `json:"stage_index,omitempty"`
	Title      string `json:"title"`
	URL        string `json:"url"`
	Snippet    string `json:"snippet,omitempty"`
}

// screenRound sizes the output budget by candidate count, then runs the
// retry loop.
func (w *WebSearcher) screenRound(ctx context.Context, llm LLM, system string, payload any, count int) (map[string]screenVerdict, Usage) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, Usage{}
	}
	maxTokens := screenMaxTokens(count)
	if w.MaxTokens > 0 && w.MaxTokens < maxTokens {
		maxTokens = w.MaxTokens
	}
	var usage Usage
	for range screenMaxAttempts {
		text, u, err := runLLM(ctx, llm, llmCall{System: system, User: string(body), MaxTokens: maxTokens})
		usage.InputTokens += u.InputTokens
		usage.OutputTokens += u.OutputTokens
		usage.CacheReadTokens += u.CacheReadTokens
		usage.CacheWriteTokens += u.CacheWriteTokens
		usage.Model = u.Model
		if err != nil {
			continue
		}
		if verdicts := parseScreenVerdicts(text); verdicts != nil {
			return verdicts, usage
		}
	}
	return nil, usage
}

// SearchUnit is the per-unit supplement search for study materials:
// verified, gated, and empty without a model — an unscreened supplement is
// worse than none.
func (w *WebSearcher) SearchUnit(ctx context.Context, query string, limit int) []Resource {
	if limit <= 0 {
		limit = 3
	}
	llm, err := w.provider()
	if err != nil || llm == nil {
		return nil
	}
	// The supplement path serves resources only; the search model's spend is
	// metered on the plan-generation path, not here.
	p, _, err := w.searchModel()
	if err != nil || p == nil || strings.TrimSpace(query) == "" {
		return nil
	}
	tool := &websearch.Tool{Factory: func() (provider.Provider, error) { return p, nil }}
	candidates, err := w.engineSearch(ctx, tool, query, limit)
	if err != nil || len(candidates) == 0 {
		return nil
	}
	firstStage := map[string]int{}
	for _, cand := range candidates {
		if cand.URL != "" {
			firstStage[cand.URL] = 0
		}
	}
	verdicts := w.verifyAll(ctx, firstStage)
	var resources []Resource
	for _, cand := range candidates {
		verdict := verdicts[cand.URL]
		if verdict.Label == "invalid" || verdict.Label == "" {
			continue
		}
		resources = append(resources, Resource{
			Title:    cand.Title,
			URL:      verdict.FinalURL,
			Verified: VerifyLabel(verdict.Label),
			Snippet:  verdict.Snippet,
		})
	}
	if len(resources) == 0 {
		return nil
	}
	payload := struct {
		Topic      string            `json:"topic"`
		Candidates []screenCandidate `json:"candidates"`
	}{Topic: truncateRunes(query, maxQueryChars)}
	for _, r := range resources {
		payload.Candidates = append(payload.Candidates, screenCandidate{
			Title:   truncateRunes(r.Title, 80),
			URL:     r.URL,
			Snippet: truncateRunes(r.Snippet, 400),
		})
	}
	verdicts2, _ := w.screenRound(ctx, llm, unitScreenPrompt, payload, len(payload.Candidates))
	if verdicts2 == nil {
		return nil
	}
	kept := applyVerdicts(resources, verdicts2)
	if len(kept) > limit {
		kept = kept[:limit]
	}
	return kept
}

package tutor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"

	"reasonix/internal/contract/config"
	"reasonix/internal/contract/provider"
	"reasonix/internal/tools/websearch"
)

// websearch.go is the production Searcher: Reasonix's unified web search
// supplies the candidates; this side adds the quotas, SSRF-guarded
// verification, and the mandatory LLM relevance gate.

const (
	maxQueryChars       = 150
	searchConcurrency   = 2
	verifyConcurrency   = 4
	screenMaxAttempts   = 2
	screenTokensCap     = 8000
	screenTokensBase    = 500
	screenTokensPerItem = 100
	baseTotalCap        = 20
	hardTotalCap        = 50
	hardStageCap        = 7
	defaultStageCap     = 5
)

// sourceURLRe pulls the <url> every Reasonix provider emits in its sources
// text; the **bold** line above it, when present, carries the title.
var sourceURLRe = regexp.MustCompile(`<((?:https?://)[^>\s]+)>`)

// searchCandidate is one engine hit.
type searchCandidate struct {
	Title string
	URL   string
}

// screenMaxTokens sizes the gate's output budget by candidate count; a fixed
// cap truncates busy replies into unparseable JSON and drops everything.
func screenMaxTokens(candidates int) int {
	return min(screenTokensCap, screenTokensPerItem*candidates+screenTokensBase)
}

// WebSearcher drives Reasonix's search, the verifier and the screening gate.
// Both models resolve per run, so a settings change applies to the next
// generation without a rebuild.
type WebSearcher struct {
	// SearchModel answers Reasonix's web-search selection: the provider the
	// agent.web_search_model config routes to, plus the ref its spend prices
	// under. (nil, "", nil) means no selection is set.
	SearchModel func() (provider.Provider, string, error)
	Provider    func() (LLM, error) // the screening gate's chat model
	Pricer      Pricer
	MaxTokens   int
	// AllowHost is a seam for tests; production leaves it zero.
	AllowHost func(rawURL string) error
}

// verifyGuard is the SSRF check applied per hop; tests swap it to admit
// loopback fixtures.
var verifyGuard = requirePublicURL

func (w *WebSearcher) guard(rawURL string) error {
	if w.AllowHost != nil {
		return w.AllowHost(rawURL)
	}
	return verifyGuard(rawURL)
}

// searchJob is one search call: a topic for one stage with its candidate
// count, which difficulty raises.
type searchJob struct {
	stageIndex int
	topic      string
	count      int
}

// SearchOutcome carries the keepers plus the warnings a person should see.
type SearchOutcome struct {
	Resources   []Resource
	Note        string
	Engine      string // the web-search model ref that served the run
	Usage       Usage  // the screening gate's metered spend
	SearchUsage Usage  // the web-search model's metered spend
}

// stageResourceCap: harder stages cite more (2+difficulty, capped).
func stageResourceCap(difficulty int) int {
	if difficulty <= 0 {
		return defaultStageCap
	}
	return min(2+difficulty, hardStageCap)
}

func totalResourceCap(nodes int) int {
	return min(baseTotalCap+max(nodes, 0), hardTotalCap)
}

func stageTopicCap(difficulty int) int {
	if difficulty >= 4 {
		return 4
	}
	return 3
}

// stageTopics derives per-stage query lists, in order, deduplicated.
func stageTopics(stages []Stage) [][]string {
	out := make([][]string, 0, len(stages))
	for _, st := range stages {
		var names []string
		seen := map[string]bool{}
		for _, t := range st.Topics {
			name := strings.TrimSpace(t.Name)
			if name == "" || seen[name] {
				continue
			}
			seen[name] = true
			names = append(names, name)
		}
		out = append(out, names)
	}
	return out
}

// parseSourceLinks reads the sources text Reasonix's providers emit — a
// "- **Title**" line above each "<url>" bullet, or bare "<url>" bullets —
// into candidates, deduplicated and capped.
func parseSourceLinks(sources string, count int) []searchCandidate {
	var out []searchCandidate
	seen := map[string]bool{}
	title := ""
	for raw := range strings.SplitSeq(sources, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "- **") && strings.HasSuffix(line, "**") {
			title = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(line, "- **"), "**"))
			continue
		}
		m := sourceURLRe.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		if seen[m[1]] {
			continue
		}
		seen[m[1]] = true
		if r := []rune(title); len(r) > 200 {
			title = string(r[:200])
		}
		out = append(out, searchCandidate{Title: title, URL: m[1]})
		title = ""
		if len(out) >= count {
			break
		}
	}
	return out
}

// engineSearch runs one query through Reasonix's websearch tool — the same
// request shape and result envelope the registry's web_search tool serves —
// and reads its sources as candidates.
func (w *WebSearcher) engineSearch(ctx context.Context, tool *websearch.Tool, query string, count int) ([]searchCandidate, error) {
	query = truncateRunes(query, maxQueryChars)
	if strings.TrimSpace(query) == "" {
		return nil, nil
	}
	args, err := json.Marshal(map[string]string{"query": query})
	if err != nil {
		return nil, err
	}
	raw, err := tool.Execute(ctx, args)
	if err != nil {
		return nil, err
	}
	var result websearch.Result
	if err := json.Unmarshal([]byte(raw), &result); err != nil {
		return nil, err
	}
	return parseSourceLinks(result.Sources, count), nil
}

// buildSearchJobs turns the plan's stage topics into capped search jobs.
func buildSearchJobs(q ResourceQuery) []searchJob {
	perStage := map[int]int{}
	var jobs []searchJob
	for stageIndex, topics := range stageTopics(q.Stages) {
		difficulty := 0
		if stageIndex < len(q.Stages) {
			difficulty = q.Stages[stageIndex].Difficulty
		}
		for _, topic := range topics {
			if perStage[stageIndex] >= stageTopicCap(difficulty) {
				break
			}
			count := 5
			if difficulty > 0 {
				count = min(3+difficulty, 8)
			}
			jobs = append(jobs, searchJob{stageIndex: stageIndex, topic: topic, count: count})
			perStage[stageIndex]++
		}
	}
	return jobs
}

// Search implements Searcher with the unified pipeline.
func (w *WebSearcher) Search(ctx context.Context, q ResourceQuery) (SearchOutcome, error) {
	outcome := SearchOutcome{}
	// The gate is mandatory: without a model there is no honest way to hand
	// out web citations, so the searcher stays empty rather than unscreened.
	llm, llmErr := w.provider()
	if llmErr != nil || llm == nil {
		outcome.Note = "未配置模型服务，本次未检索网络资源（引文必须经 AI 相关性筛选）"
		return outcome, nil
	}
	p, ref, err := w.searchModel()
	if err != nil {
		reason, _ := config.WebSearchReasonOf(err)
		if reason == "" {
			outcome.Note = fmt.Sprintf("联网检索模型不可用（%v），本次未检索网络资源", err)
		} else {
			outcome.Note = fmt.Sprintf("联网检索模型不可用（%s），请在模型偏好中重新选择，本次未检索网络资源", reason)
		}
		return outcome, nil
	}
	if p == nil {
		outcome.Note = "未选择联网检索模型（模型偏好 → Web search），本次未检索网络资源"
		return outcome, nil
	}
	outcome.Engine = ref

	// One tool instance serves the whole run; the search model's spend is
	// metered alongside the gate's so the daily budget sees it.
	var mu sync.Mutex
	spend := Usage{Model: ref}
	tool := &websearch.Tool{
		Factory: func() (provider.Provider, error) { return p, nil },
		ReportUsage: func(u *provider.Usage) {
			if u == nil {
				return
			}
			mu.Lock()
			defer mu.Unlock()
			spend.addChunkUsage(*u)
		},
	}

	jobs := buildSearchJobs(q)
	results := w.runQueries(ctx, tool, jobs)
	succeeded := 0
	for _, r := range results {
		if r != nil {
			succeeded++
		}
	}
	if len(jobs) > 0 && succeeded == 0 {
		outcome.Note = fmt.Sprintf("联网检索模型（%s）未返回结果；请检查所选模型或稍后重试", ref)
		return outcome, nil
	}

	resources := w.screenableResources(ctx, q, jobs, results)
	if len(resources) == 0 {
		return outcome, nil
	}
	screened, note, usage := w.screen(ctx, llm, q, resources)
	outcome.Resources = screened
	outcome.Note = note
	outcome.Usage = usage
	outcome.SearchUsage = spend
	for i := range outcome.Resources {
		outcome.Resources[i].Snippet = ""
	}
	return outcome, nil
}

func (w *WebSearcher) provider() (LLM, error) {
	if w.Provider == nil {
		return nil, errors.New("tutor: no llm provider")
	}
	return w.Provider()
}

func (w *WebSearcher) searchModel() (provider.Provider, string, error) {
	if w.SearchModel == nil {
		return nil, "", nil
	}
	return w.SearchModel()
}

// screenableResources verifies the deduplicated pool and keeps the valid,
// per-stage-capped survivors for the gate.
func (w *WebSearcher) screenableResources(ctx context.Context, q ResourceQuery, jobs []searchJob, results [][]searchCandidate) []Resource {
	type stagedCandidate struct {
		stageIndex int
		candidate  searchCandidate
	}
	var pool []stagedCandidate
	firstStage := map[string]int{}
	for i, job := range jobs {
		if results[i] == nil {
			continue
		}
		for _, cand := range results[i] {
			if cand.URL == "" {
				continue
			}
			if _, seen := firstStage[cand.URL]; !seen {
				firstStage[cand.URL] = job.stageIndex
				pool = append(pool, stagedCandidate{stageIndex: job.stageIndex, candidate: cand})
			}
		}
	}
	verdicts := w.verifyAll(ctx, firstStage)
	var resources []Resource
	perStage := map[int]int{}
	for _, item := range pool {
		if len(resources) >= totalResourceCap(q.Nodes) {
			break
		}
		difficulty := 0
		if item.stageIndex < len(q.Stages) {
			difficulty = q.Stages[item.stageIndex].Difficulty
		}
		if perStage[item.stageIndex] >= stageResourceCap(difficulty) {
			continue
		}
		verdict := verdicts[item.candidate.URL]
		if verdict.Label == "invalid" || verdict.Label == "" {
			continue
		}
		resources = append(resources, Resource{
			StageIndex: item.stageIndex,
			Title:      item.candidate.Title,
			URL:        verdict.FinalURL,
			Type:       "doc",
			Verified:   VerifyLabel(verdict.Label),
			Note:       "",
			Snippet:    verdict.Snippet,
		})
		perStage[item.stageIndex]++
	}
	return resources
}

// runQueries fans the search calls out at the searched-for concurrency,
// keeping each failure local: one dead topic never kills the batch.
func (w *WebSearcher) runQueries(ctx context.Context, tool *websearch.Tool, jobs []searchJob) [][]searchCandidate {
	results := make([][]searchCandidate, len(jobs))
	sem := make(chan struct{}, searchConcurrency)
	var wg sync.WaitGroup
	for i, job := range jobs {
		wg.Add(1)
		go func(i int, topic string, count int) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			found, err := w.engineSearch(ctx, tool, topic, count)
			if err == nil {
				results[i] = found
			}
		}(i, job.topic, job.count)
	}
	wg.Wait()
	return results
}

// verifyClient answers the fetch client for verification: dial-time guarded
// in production, plain under the test seam (loopback fixtures).
func (w *WebSearcher) verifyClient() *http.Client {
	if w.AllowHost != nil {
		return &http.Client{Timeout: verifyTimeout}
	}
	return publicOnlyClient(verifyTimeout)
}

// verifyAll checks the deduplicated candidate URLs concurrently.
func (w *WebSearcher) verifyAll(ctx context.Context, firstStage map[string]int) map[string]verifyVerdict {
	urls := make([]string, 0, len(firstStage))
	for u := range firstStage {
		urls = append(urls, u)
	}
	sort.Strings(urls)
	verdicts := make(map[string]verifyVerdict, len(urls))
	sem := make(chan struct{}, verifyConcurrency)
	client := w.verifyClient()
	var mu sync.Mutex
	var wg sync.WaitGroup
	for _, rawURL := range urls {
		wg.Add(1)
		go func(rawURL string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			verdict := verifyURL(ctx, client, rawURL, w.guard)
			mu.Lock()
			verdicts[rawURL] = verdict
			mu.Unlock()
		}(rawURL)
	}
	wg.Wait()
	return verdicts
}

func truncateRunes(s string, n int) string {
	r := []rune(strings.TrimSpace(s))
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

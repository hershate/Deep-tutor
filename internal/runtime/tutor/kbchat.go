package tutor

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// kbchat.go — knowledge-base问答, Deep-tutor's /kb/chat: documents injected
// as capped context, one kb_answer model call, citations mapped from the
// answer's used ids, and every success persisted as a self-contained record.

const (
	// kbChatDocChars caps one document's injected text (+truncation marker
	// stays under the prompt field cap), kbChatTotalChars the whole context.
	kbChatDocChars     = 19_900
	kbChatTotalChars   = 150_000
	kbChatDefaultDocs  = 20 // default context: newest parsed documents
	kbChatMaxDocs      = 20 // explicit document_ids ceiling
	kbQuestionMaxChars = 4000
	kbChatTimeout      = 120 * time.Second
)

// ErrUpstreamTimeout and ErrBadGateway map the LLM failure classes; the
// refusal layer never echoes provider detail (Deep-tutor R35).
var (
	ErrUpstreamTimeout = errors.New("tutor: llm response timed out")
	ErrBadGateway      = errors.New("tutor: llm call failed")
)

// KbChatCitation names one document the answer drew on.
type KbChatCitation struct {
	DocID string `json:"doc_id"`
	Title string `json:"title"`
}

// KbChatRecord is one persisted Q&A: citations and omitted titles stored as
// snapshots, so a later document delete leaves the record readable.
type KbChatRecord struct {
	ID            string           `json:"id"`
	Question      string           `json:"question"`
	Answer        string           `json:"answer"`
	Citations     []KbChatCitation `json:"citations"`
	OmittedTitles []string         `json:"omitted_titles"`
	DocIDs        []string         `json:"doc_ids,omitempty"`
	ContextDocs   int              `json:"context_docs"`
	CreatedAt     time.Time        `json:"created_at"`
}

// KbChatResult is the chat endpoint's answer.
type KbChatResult struct {
	RecordID      string           `json:"record_id"`
	Answer        string           `json:"answer"`
	Citations     []KbChatCitation `json:"citations"`
	OmittedTitles []string         `json:"omitted_titles"`
	ContextDocs   int              `json:"context_docs"`
}

// kbAnswerSpec is the model's output contract.
type kbAnswerSpec struct {
	Answer     string   `json:"answer"`
	UsedDocIDs []string `json:"used_doc_ids"`
}

const kbAnswerPrompt = `你是 Reasonix 的「知识库问答」工具。任务：仅依据用户知识库中给定的文档内容，回答用户问题。非交互，直接输出规约。

输入：user message 的 args.question（用户问题）与 args.documents（参考文档列表：[{"doc_id":文档id,"title":"标题","text":"解析文本"}]，text 尾部可能带「…[已截断]」标注表示原文未完）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"answer":"回答正文（中文 Markdown）","used_doc_ids":[实际参考的文档id]}

规则：
- 仅依据 args.documents 中的内容回答；文档里没有的信息不要编造，也不要用外部知识补充。
- 文档内容不足以回答时，在 answer 中明确说明「知识库文档中没有足够信息」，并指出还缺少什么。
- answer 用中文 Markdown 组织（适度使用要点列表/小标题），直接回答问题本身。
- 引用具体文档内容时以「《标题》」标注出处。
- used_doc_ids 只列实际用于回答的 doc_id（必须是 args.documents 中存在的 id）；一个都没用到则返回空列表。

仅返回符合 schema 的 JSON 对象。`

// kbContextDoc is one injected document.
type kbContextDoc struct {
	DocID string `json:"doc_id"`
	Title string `json:"title"`
	Text  string `json:"text"`
}

// assembleKbContext builds the injectable payload: per-doc ≤19.9k with a
// truncation marker, total ≤150k; documents dropped for a missing text or an
// exhausted budget land in omitted so nothing disappears silently.
func (s *Service) assembleKbContext(docs []KbDoc) ([]kbContextDoc, []string) {
	payload := []kbContextDoc{}
	omitted := []string{}
	total := 0
	for i := range docs {
		d := &docs[i]
		text, err := s.store.ReadKbParsed(d.ID)
		if err != nil || strings.TrimSpace(text) == "" {
			omitted = append(omitted, d.Title)
			continue
		}
		runes := []rune(text)
		if len(runes) > kbChatDocChars {
			text = string(runes[:kbChatDocChars]) + "…[已截断]"
		}
		if total+len([]rune(text)) > kbChatTotalChars {
			omitted = append(omitted, d.Title)
			continue
		}
		total += len([]rune(text))
		payload = append(payload, kbContextDoc{DocID: d.ID, Title: d.Title, Text: text})
	}
	return payload, omitted
}

// KbChat answers one question strictly from library documents.
func (s *Service) KbChat(ctx context.Context, question string, docIDs []string) (KbChatResult, error) {
	question = strings.TrimSpace(question)
	if question == "" {
		return KbChatResult{}, ErrKbEmpty
	}
	if len([]rune(question)) > kbQuestionMaxChars {
		return KbChatResult{}, fmt.Errorf("%w: question exceeds %d chars", ErrKbTooLarge, kbQuestionMaxChars)
	}
	if len(docIDs) > kbChatMaxDocs {
		return KbChatResult{}, fmt.Errorf("%w: at most %d documents", ErrKbTooLarge, kbChatMaxDocs)
	}

	var docs []KbDoc
	explicit := len(docIDs) > 0
	if explicit {
		ordered := []string{}
		seen := map[string]bool{}
		for _, id := range docIDs {
			if !seen[id] {
				seen[id] = true
				ordered = append(ordered, id)
			}
		}
		all, err := s.store.KbDocs()
		if err != nil {
			return KbChatResult{}, err
		}
		byID := map[string]*KbDoc{}
		for i := range all {
			byID[all[i].ID] = &all[i]
		}
		for _, id := range ordered {
			d, ok := byID[id]
			if !ok {
				return KbChatResult{}, fmt.Errorf("%w: document %s does not exist", ErrNotFound, id)
			}
			docs = append(docs, *d)
		}
	} else {
		all, err := s.store.KbDocs()
		if err != nil {
			return KbChatResult{}, err
		}
		for i := range all {
			if all[i].Status == "parsed" {
				docs = append(docs, all[i])
				if len(docs) >= kbChatDefaultDocs {
					break
				}
			}
		}
	}

	payload, omitted := s.assembleKbContext(docs)
	if len(payload) == 0 {
		return KbChatResult{}, ErrKbNoDocs
	}
	if s.budgetExceeded() {
		return KbChatResult{}, ErrBudgetExceeded
	}
	llm, err := s.opt.Provider()
	if err != nil {
		return KbChatResult{}, ErrNoLLM
	}

	chatCtx, cancel := context.WithTimeout(ctx, kbChatTimeout)
	defer cancel()
	spec, usage, callErr := s.llmKbAnswer(chatCtx, llm, question, payload)
	if cost := s.kbUsageCost(usage); cost > 0 {
		// Meter even the failed path — tokens were spent either way.
		_ = s.store.AddUsage(s.opt.now().Format("2006-01-02"), cost)
	}
	if callErr != nil {
		if errors.Is(callErr, ErrNoLLM) {
			return KbChatResult{}, ErrNoLLM
		}
		if errors.Is(callErr, context.DeadlineExceeded) || isNetTimeout(callErr) {
			return KbChatResult{}, ErrUpstreamTimeout
		}
		return KbChatResult{}, ErrBadGateway
	}

	// Citation whitelist: only ids the context actually carried survive,
	// so the model cannot reference a document it was never given.
	allowed := map[string]string{}
	for _, d := range payload {
		allowed[d.DocID] = d.Title
	}
	citations := []KbChatCitation{}
	usedSeen := map[string]bool{}
	for _, id := range spec.UsedDocIDs {
		if title, ok := allowed[id]; ok && !usedSeen[id] {
			usedSeen[id] = true
			citations = append(citations, KbChatCitation{DocID: id, Title: title})
		}
	}
	record := &KbChatRecord{
		Question: question, Answer: spec.Answer, Citations: citations,
		OmittedTitles: omitted, ContextDocs: len(payload),
	}
	if explicit {
		for _, d := range payload {
			record.DocIDs = append(record.DocIDs, d.DocID)
		}
	}
	if err := s.store.SaveKbChat(record); err != nil {
		return KbChatResult{}, err
	}
	return KbChatResult{
		RecordID: record.ID, Answer: spec.Answer, Citations: citations,
		OmittedTitles: omitted, ContextDocs: len(payload),
	}, nil
}

// llmKbAnswer runs the kb_answer call. No degraded default exists — an
// unanswered question must fail honestly, never fabricate.
func (s *Service) llmKbAnswer(ctx context.Context, llm LLM, question string, payload []kbContextDoc) (kbAnswerSpec, Usage, error) {
	var spec kbAnswerSpec
	docMaps := make([]map[string]any, 0, len(payload))
	for _, d := range payload {
		docMaps = append(docMaps, map[string]any{"doc_id": d.DocID, "title": d.Title, "text": d.Text})
	}
	args := map[string]any{"question": question, "documents": docMaps}
	tool := jsonTool[kbAnswerSpec]{
		System: kbAnswerPrompt,
		User: func() string {
			return mustJSON(args) + "\n仅返回符合 schema 的 JSON 对象（不要 markdown 代码块、不要解释）。"
		},
		Decode: decodeStrict[kbAnswerSpec],
	}
	spec, usage, _, err := runJSONTool(ctx, llm, s.opt.MaxTokens, tool)
	return spec, usage, err
}

func isNetTimeout(err error) bool {
	var ne net.Error
	return errors.As(err, &ne) && ne.Timeout()
}

func (s *Service) kbUsageCost(u Usage) float64 {
	if s.opt.Pricer == nil {
		return 0
	}
	return s.opt.Pricer(u.Model, u)
}

// Chat records.

func (s *Store) SaveKbChat(r *KbChatRecord) error {
	if r.ID == "" {
		r.ID = newID()
		r.CreatedAt = time.Now()
	}
	if r.Citations == nil {
		r.Citations = []KbChatCitation{}
	}
	if r.OmittedTitles == nil {
		r.OmittedTitles = []string{}
	}
	return s.writeJSON(filepath.Join(s.kbChatsDir(), r.ID+".json"), r)
}

func (s *Store) KbChats() ([]KbChatRecord, error) {
	entries, err := os.ReadDir(s.kbChatsDir())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []KbChatRecord
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		var r KbChatRecord
		if err := s.readJSON(filepath.Join(s.kbChatsDir(), e.Name()), &r); err == nil {
			out = append(out, r)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].CreatedAt.Equal(out[j].CreatedAt) {
			return out[i].CreatedAt.After(out[j].CreatedAt)
		}
		return out[i].ID > out[j].ID
	})
	return out, nil
}

func (s *Store) DeleteKbChat(id string) error {
	if err := os.Remove(filepath.Join(s.kbChatsDir(), id+".json")); err != nil {
		if os.IsNotExist(err) {
			return ErrNotFound
		}
		return err
	}
	return nil
}

// KbChatList filters records by a question substring, newest first, with
// the total the pager needs.
func (s *Service) KbChatList(q string, limit, offset int) (map[string]any, error) {
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	if offset < 0 {
		offset = 0
	}
	records, err := s.store.KbChats()
	if err != nil {
		return nil, err
	}
	if q = strings.TrimSpace(q); q != "" {
		kept := records[:0]
		for _, r := range records {
			if strings.Contains(r.Question, q) {
				kept = append(kept, r)
			}
		}
		records = kept
	}
	total := len(records)
	if offset < total {
		records = records[offset:]
	} else {
		records = nil
	}
	if len(records) > limit {
		records = records[:limit]
	}
	if records == nil {
		records = []KbChatRecord{}
	}
	return map[string]any{"items": records, "total": total}, nil
}

// KbChatDelete removes one record; a missing id is the same refusal as
// anyone else's, so ids cannot be probed.
func (s *Service) KbChatDelete(id string) error {
	return s.store.DeleteKbChat(id)
}

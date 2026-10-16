package tutor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"reasonix/internal/contract/provider"
)

// LLM is the provider surface the pipeline drives. It is satisfied by
// provider.Provider instances resolved by boot.
type LLM interface {
	Name() string
	Stream(ctx context.Context, req provider.Request) (<-chan provider.Chunk, error)
}

// ErrNoLLM marks generation without a configured model; the pipeline degrades
// rather than guessing.
var ErrNoLLM = errors.New("tutor: no llm provider")

// Usage is the metered cost of one tool run. Cost is filled by the injected
// pricer when one is wired; token counts are always present.
type Usage struct {
	InputTokens      int64
	OutputTokens     int64
	CacheReadTokens  int64
	CacheWriteTokens int64
	Cost             float64
	Model            string
}

func (u *Usage) addChunkUsage(cu provider.Usage) {
	u.InputTokens += int64(cu.PromptTokens)
	u.OutputTokens += int64(cu.CompletionTokens)
	u.CacheReadTokens += int64(cu.CacheHitTokens)
	u.CacheWriteTokens += int64(cu.CacheWriteTokens)
}

// Pricer converts metered usage into a cost number for the budget guard.
type Pricer func(model string, u Usage) float64

// llmCall is one non-agentic completion: a stable system prompt plus a user
// turn, accumulated from the stream. Transport retries live in the provider
// adapters, so this layer only handles shape repair.
type llmCall struct {
	System    string
	User      string
	MaxTokens int
}

func runLLM(ctx context.Context, llm LLM, call llmCall) (text string, usage Usage, err error) {
	if llm == nil {
		return "", usage, ErrNoLLM
	}
	req := provider.Request{
		Messages: []provider.Message{
			{Role: provider.RoleSystem, Content: call.System},
			{Role: provider.RoleUser, Content: call.User},
		},
		MaxTokens: call.MaxTokens,
	}
	ch, err := llm.Stream(ctx, req)
	if err != nil {
		return "", usage, err
	}
	usage.Model = llm.Name()
	var sb strings.Builder
	for {
		select {
		case chunk, ok := <-ch:
			if !ok {
				return sb.String(), usage, nil
			}
			switch chunk.Type {
			case provider.ChunkText:
				sb.WriteString(chunk.Text)
			case provider.ChunkUsage:
				if chunk.Usage != nil {
					usage.addChunkUsage(*chunk.Usage)
				}
			case provider.ChunkError:
				if chunk.Err != nil {
					return sb.String(), usage, chunk.Err
				}
			}
		case <-ctx.Done():
			// A provider whose stream ignores the context cannot hold the
			// pipeline hostage; cancellation lands here.
			return sb.String(), usage, ctx.Err()
		}
	}
}

// extractJSON pulls a JSON object out of a model reply: fenced ```json
// blocks first, then the outermost braces. An empty candidate is an error.
func extractJSON(text string) (string, error) {
	trimmed := strings.TrimSpace(text)
	if trimmed == "" {
		return "", errors.New("tutor: empty model reply")
	}
	if _, rest, ok := strings.Cut(trimmed, "```json"); ok {
		if cand, _, found := strings.Cut(rest, "```"); found {
			if clean := strings.TrimSpace(cand); clean != "" {
				return clean, nil
			}
		}
	}
	first := strings.Index(trimmed, "{")
	last := strings.LastIndex(trimmed, "}")
	if first < 0 || last <= first {
		return "", errors.New("tutor: no JSON object in model reply")
	}
	return trimmed[first : last+1], nil
}

// jsonTool describes one structured LLM step: a skill or inline prompt, the
// user-turn renderer, a strict decoder, and the degraded default. Repair
// feedback re-asks up to repairAttempts times before degrading.
type jsonTool[T any] struct {
	System   string
	User     func() string
	Decode   func(raw json.RawMessage) (T, error)
	Verified func(v T) error
	Default  func() T
}

const repairAttempts = 3

// runJSONTool executes the repair loop: decode failures feed the exact error
// back to the model; exhaustion either repairs with the tool default or
// fails the pipeline step.
func runJSONTool[T any](ctx context.Context, llm LLM, maxTokens int, tool jsonTool[T]) (value T, usage Usage, degraded string, err error) {
	feedback := ""
	for attempt := 0; attempt <= repairAttempts; attempt++ {
		user := tool.User()
		if feedback != "" {
			user = user + "\n\n上一次输出无法通过校验，错误：\n" + feedback + "\n请修正后仅重新输出符合 schema 的 JSON 对象。"
		}
		text, u, callErr := runLLM(ctx, llm, llmCall{System: tool.System, User: user, MaxTokens: maxTokens})
		usage.InputTokens += u.InputTokens
		usage.OutputTokens += u.OutputTokens
		usage.CacheReadTokens += u.CacheReadTokens
		usage.CacheWriteTokens += u.CacheWriteTokens
		usage.Model = u.Model
		if callErr != nil {
			if errors.Is(callErr, ErrNoLLM) {
				var zero T
				if tool.Default != nil {
					return tool.Default(), usage, "no_llm_provider", nil
				}
				return zero, usage, "", callErr
			}
			return value, usage, "", callErr
		}
		raw, extErr := extractJSON(text)
		if extErr != nil {
			feedback = extErr.Error()
			continue
		}
		decoded, dErr := tool.Decode(json.RawMessage(raw))
		if dErr != nil {
			feedback = dErr.Error()
			continue
		}
		if tool.Verified != nil {
			if vErr := tool.Verified(decoded); vErr != nil {
				feedback = vErr.Error()
				continue
			}
		}
		return decoded, usage, "", nil
	}
	if tool.Default != nil {
		return tool.Default(), usage, "schema_violation", nil
	}
	return value, usage, "", fmt.Errorf("tutor: schema violation after %d repair attempts", repairAttempts)
}

func decodeStrict[T any](raw json.RawMessage) (T, error) {
	var v T
	if err := json.Unmarshal(raw, &v); err != nil {
		return v, fmt.Errorf("tutor: decode: %w", err)
	}
	return v, nil
}

// toolMeta carries per-call accounting back to the orchestrator.
type toolMeta struct {
	DurationMs int64   `json:"duration_ms,omitempty"`
	Tokens     int64   `json:"tokens,omitempty"`
	Cost       float64 `json:"cost,omitempty"`
	Model      string  `json:"model,omitempty"`
	Degraded   string  `json:"degraded,omitempty"`
}

func metaFrom(u Usage, p Pricer, start time.Time) toolMeta {
	tokens := u.InputTokens + u.OutputTokens
	cost := 0.0
	if p != nil {
		cost = p(u.Model, u)
	}
	return toolMeta{
		DurationMs: time.Since(start).Milliseconds(),
		Tokens:     tokens,
		Cost:       cost,
		Model:      u.Model,
	}
}

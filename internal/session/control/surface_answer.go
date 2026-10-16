package control

import (
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/surfacespec"
)

func (a *approvalManager) resolveAskAnswer(id string, answers []event.AskAnswer) (pendingAsk, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	p, ok := a.asks[id]
	if !ok {
		return pendingAsk{}, surfacespec.ErrExpired
	}
	if err := validateSurfaceAnswer(p.questions, answers); err != nil {
		return pendingAsk{}, err
	}
	delete(a.asks, id)
	return p, nil
}

func validateSurfaceAnswer(questions []event.AskQuestion, answers []event.AskAnswer) error {
	for _, q := range questions {
		if q.Surface == nil {
			continue
		}
		if len(answers) == 0 {
			return nil
		}
		if len(questions) != 1 || len(answers) != 1 || answers[0].QuestionID != q.ID || len(answers[0].Selected) != 0 {
			return surfacespec.ErrValueInvalid
		}
		_, err := q.Surface.ParseAnswers(answers[0].Value)
		return err
	}
	for _, answer := range answers {
		if len(answer.Value) != 0 {
			return surfacespec.ErrValueInvalid
		}
	}
	return nil
}

import type { SessionEvent } from "./session";
import type { SessionState } from "./session_types";

export function measureInteractions(s: SessionState, ev: SessionEvent): SessionState {
  if (ev.kind === "ask_request" && ev.ask && !s.interactionIDs.asked.includes(ev.ask.id)) {
    const { id, questions } = ev.ask;
    return {
      ...s,
      interactionIDs: { ...s.interactionIDs, asked: [...s.interactionIDs.asked, id] },
      interactions: {
        ...s.interactions,
        requests: s.interactions.requests + 1,
        questions: s.interactions.questions + questions.length,
        options: s.interactions.options + questions.reduce((n, q) => n + q.options.length, 0),
      },
    };
  }
  if (ev.kind !== "__decided" || !ev.answers) return s;
  const item = s.items.find(i => i.id === ev.id);
  if (item?.t !== "ask" || s.interactionIDs.answered.includes(item.ask.id)) return s;
  const custom = ev.answers.some((values, j) => values.some(value =>
    value.trim() !== "" && !item.ask.questions[j]?.options.some(option => option.label === value)));
  return {
    ...s,
    interactionIDs: { ...s.interactionIDs, answered: [...s.interactionIDs.answered, item.ask.id] },
    interactions: {
      ...s.interactions,
      answers: s.interactions.answers + 1,
      customAnswers: s.interactions.customAnswers + (custom ? 1 : 0),
    },
  };
}

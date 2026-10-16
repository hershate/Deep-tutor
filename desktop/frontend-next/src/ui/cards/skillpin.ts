export interface SkillPin {
  name: string;
  description: string;
  args: string;
  // The whole wrapper as the model received it, for the reader who opens it.
  text: string;
}

// The host wraps every skill invocation in one form (`<skill-pin name="…">` …
// `</skill-pin>`, internal/ext/skill RenderInvocation). Only a message that is
// exactly that wrapper is an invocation; the same tag inside other text is text.
const WRAPPER = /^<skill-pin name=("(?:[^"\\]|\\.)*")>\n([\s\S]*)\n<\/skill-pin>$/;
const ARGS = "\n\nArguments: ";

export function parseSkillPin(text: string): SkillPin | null {
  const m = WRAPPER.exec(text);
  if (!m) return null;
  let name: string;
  try {
    name = JSON.parse(m[1]);
  } catch {
    return null;
  }
  const inner = m[2];
  const at = inner.lastIndexOf(ARGS);
  const args = at >= 0 ? inner.slice(at + ARGS.length).trim() : "";
  const described = /^# Skill: [^\n]*\n> ([^\n]*)/.exec(inner);
  return { name, description: described?.[1] ?? "", args, text };
}

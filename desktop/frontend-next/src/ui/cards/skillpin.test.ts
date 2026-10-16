import { describe, expect, it } from "vitest";
import { parseSkillPin } from "./skillpin";

const pin = (name: string, body: string, args = "") =>
  `<skill-pin name=${JSON.stringify(name)}>\n# Skill: ${name}\n> A short description.\n(scope: user · /home/me/.reasonix/skills/${name}/SKILL.md)\n\n${body}${args ? `\n\nArguments: ${args}` : ""}\n</skill-pin>`;

describe("parseSkillPin", () => {
  it("reads the name, the one-line description and the arguments the user typed", () => {
    const got = parseSkillPin(pin("code-quality", "Review for correctness.\n\n## Steps\n1. read", "the auth module"));
    expect(got).toMatchObject({ name: "code-quality", description: "A short description.", args: "the auth module" });
    expect(got?.text).toContain("## Steps");
  });

  it("has no arguments when the user typed none", () => {
    expect(parseSkillPin(pin("review", "Body."))?.args).toBe("");
  });

  it("keeps a name that needs quoting", () => {
    expect(parseSkillPin(pin('we"ird', "Body."))?.name).toBe('we"ird');
  });

  it("is not a skill invocation unless the whole message is the wrapper", () => {
    expect(parseSkillPin("please explain <skill-pin name=\"x\">\n# Skill: x\n</skill-pin>")).toBeNull();
    expect(parseSkillPin(pin("x", "Body.") + "\ntrailing words")).toBeNull();
    expect(parseSkillPin("plain text")).toBeNull();
    expect(parseSkillPin("")).toBeNull();
  });
});

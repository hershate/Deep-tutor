// @vitest-environment jsdom
import "./testkit";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Item } from "../state/session";
import type { Checkpoint } from "../port/port";
import { UserCard } from "./cards/UserCard";

afterEach(cleanup);

const cp: Checkpoint = { turn: 4, prompt: "", files: 2, msgIndex: 7 };
const pin = (args = "") =>
  `<skill-pin name="code-quality">\n# Skill: code-quality\n> Reviews code quality.\n(scope: user · /home/me/.reasonix/skills/code-quality/SKILL.md)\n\nLONG SKILL BODY LINE\n${"more ".repeat(40)}${args ? `\n\nArguments: ${args}` : ""}\n</skill-pin>`;
const draw = (text: string) => {
  const item = { t: "user", id: "row", text } as Extract<Item, { t: "user" }>;
  render(<UserCard item={item} cp={cp} onResend={vi.fn()} onPrepareRewind={vi.fn()} onCommitRewind={vi.fn()} />);
};

describe("a message that is a skill invocation", () => {
  it("shows the skill's name and what the person typed, not the skill's whole text", () => {
    draw(pin("the auth module"));
    expect(screen.getByText("code-quality")).toBeTruthy();
    expect(screen.getByText("the auth module")).toBeTruthy();
    const details = document.querySelector("details.skillpin") as HTMLDetailsElement;
    expect(details.open).toBe(false);
  });

  it("opens to the skill's own text", async () => {
    draw(pin());
    await userEvent.click(document.querySelector("details.skillpin > summary")!);
    expect((document.querySelector("details.skillpin") as HTMLDetailsElement).open).toBe(true);
    expect(document.querySelector(".skillpin-text")?.textContent).toContain("LONG SKILL BODY LINE");
    expect(document.querySelector(".skillpin-desc")?.textContent).toBe("Reviews code quality.");
  });

  it("leaves an ordinary message, and one that merely mentions the tag, as plain text", () => {
    draw("review src/ui please");
    expect(document.querySelector("details.skillpin")).toBeNull();
    expect(screen.getByText("review src/ui please")).toBeTruthy();
    cleanup();
    draw('what is a <skill-pin name="x"> block?');
    expect(document.querySelector("details.skillpin")).toBeNull();
  });
});

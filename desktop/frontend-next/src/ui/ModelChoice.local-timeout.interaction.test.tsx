// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import "./testkit";
import { ModelChoice, type ModelFact } from "./ModelChoice";

afterEach(cleanup);

const draw = (fact: ModelFact) =>
  render(
    <ModelChoice
      models={["clef:27b"]} picked={["clef:27b"]} vision={[]} def="clef:27b"
      onDefault={vi.fn()} onVision={vi.fn()} facts={{ "clef:27b": fact }} onCheck={vi.fn()} onToggle={vi.fn()} onAdd={vi.fn()}
    />,
  );

it("says a local engine did not answer, and that the model may still be loading", () => {
  draw({ origin: "configured", status: "unknown", reason: "local_timeout" });
  expect(document.body.textContent).toContain("本机服务没有在时限内回答");
  expect(document.body.textContent).toContain("可能还在加载");
});

it("keeps the plain wording for a remote timeout", () => {
  draw({ origin: "configured", status: "unknown", reason: "timeout" });
  expect(document.body.textContent).toContain("验证超时，尚未确认");
  expect(document.body.textContent).not.toContain("本机服务");
});

import { describe, expect, it } from "vitest";
import { layoutGraph } from "./gitgraph";
import type { GitCommit } from "../port/workspace";

const c = (id: string, parents: string[] = []): GitCommit => ({ id, parents, author: "a", at: 0, refs: [], subject: id });
const shape = (commits: GitCommit[]) => layoutGraph(commits).rows.map((r) => ({ id: r.commit.id, lane: r.lane, through: r.through, from: r.from, to: r.to }));

describe("the commit graph's lanes", () => {
  it("keeps a straight history in one column", () => {
    const out = layoutGraph([c("3", ["2"]), c("2", ["1"]), c("1")]);
    expect(out.lanes).toBe(1);
    expect(out.rows.map((r) => r.lane)).toEqual([0, 0, 0]);
    expect(out.rows[0]).toMatchObject({ from: [], to: [0] });
    expect(out.rows[1]).toMatchObject({ from: [0], to: [0] });
    expect(out.rows[2]).toMatchObject({ from: [0], to: [] });
  });

  it("gives a branch its own lane from the merge down to where it forks", () => {
    // m merges side (s2 -> s1) into main (a2 -> a1), both forked from base.
    const rows = shape([c("m", ["a2", "s2"]), c("s2", ["s1"]), c("a2", ["a1"]), c("s1", ["base"]), c("a1", ["base"]), c("base")]);
    expect(rows).toEqual([
      { id: "m", lane: 0, through: [], from: [], to: [0, 1] },
      { id: "s2", lane: 1, through: [0], from: [1], to: [1] },
      { id: "a2", lane: 0, through: [1], from: [0], to: [0] },
      { id: "s1", lane: 1, through: [0], from: [1], to: [1] },
      { id: "a1", lane: 0, through: [1], from: [0], to: [0] },
      // both lanes were heading to base: it takes the first, the other ends in it
      { id: "base", lane: 0, through: [], from: [0, 1], to: [] },
    ]);
  });

  it("lets two lanes converge on one commit, which takes the lowest", () => {
    const out = layoutGraph([c("x", ["p"]), c("y", ["p"]), c("p")]);
    expect(out.rows[1]).toMatchObject({ lane: 1, to: [1] });
    expect(out.lanes).toBe(2);
    expect(out.rows[2]).toMatchObject({ lane: 0, from: [0, 1] });
  });

  it("joins a merge's other parent to the lane already heading there", () => {
    // m's second parent b is already awaited by lane 1 (from n), so m draws to it.
    const rows = shape([c("n", ["b"]), c("m", ["a", "b"]), c("a", ["b"]), c("b")]);
    expect(rows[1]).toMatchObject({ id: "m", lane: 1, to: [1, 0] });
  });

  it("reuses a freed lane for the next branch", () => {
    const rows = layoutGraph([c("m", ["a", "b"]), c("b", ["z"]), c("a", ["z"]), c("z", ["w"]), c("n", ["w"]), c("w")]);
    expect(rows.lanes).toBe(2);
  });

  it("follows parents that fall outside the page to the bottom", () => {
    const out = layoutGraph([c("t", ["gone"]), c("u", ["gone2"])]);
    expect(out.rows[1]).toMatchObject({ lane: 1, through: [0] });
    expect(out.lanes).toBe(2);
  });

  it("is deterministic and empty-safe", () => {
    const input = [c("m", ["a", "b"]), c("b", ["a"]), c("a")];
    expect(layoutGraph(input)).toEqual(layoutGraph(input));
    expect(layoutGraph([])).toEqual({ rows: [], lanes: 1 });
  });
});

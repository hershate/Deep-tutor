import type { GitCommit } from "../port/workspace";

/** One row of the commit graph, ready to draw. Lanes are columns counted from the
 *  left, and every line is described by the lanes it joins in this row. */
export interface GraphRow {
  commit: GitCommit;
  lane: number;
  // Lanes with a line running straight through the row, not touching the commit.
  through: number[];
  // Lanes whose line comes down into this commit from the row above: its own
  // lane when it was expected, and every other lane that was waiting for it.
  from: number[];
  // Lanes the commit's lines leave toward, one per parent.
  to: number[];
}

/** Lays a newest-first list of commits out in lanes. A lane waits for one commit,
 *  the parent its line is heading to; a commit takes the first lane waiting for
 *  it and sends its first parent down that lane, so a branch keeps its column
 *  until it merges, and where lanes converge the lowest one carries on. Parents
 *  past the end of the page are followed to the bottom. Pure: the same commits
 *  always give the same rows. */
export function layoutGraph(commits: GitCommit[]): { rows: GraphRow[]; lanes: number } {
  const waiting: (string | null)[] = [];
  const rows: GraphRow[] = [];
  let lanes = 1;
  const free = () => {
    const at = waiting.indexOf(null);
    if (at >= 0) return at;
    waiting.push(null);
    return waiting.length - 1;
  };
  for (const commit of commits) {
    const from = waiting.flatMap((id, lane) => (id === commit.id ? [lane] : []));
    const lane = from[0] ?? free();
    const through = waiting.flatMap((id, i) => (id !== null && id !== commit.id ? [i] : []));
    for (const i of from) waiting[i] = null;
    const to: number[] = [];
    commit.parents.forEach((parent, n) => {
      // The first parent always continues this commit's own lane; two lanes may
      // wait for one commit and both end in it. A merge's other parents join a
      // lane that already heads there instead of opening one more.
      const open = waiting.indexOf(parent);
      if (n > 0 && open >= 0) {
        to.push(open);
        return;
      }
      const target = n === 0 ? lane : free();
      waiting[target] = parent;
      to.push(target);
    });
    rows.push({ commit, lane, through, from, to });
    lanes = Math.max(lanes, waiting.length, lane + 1);
  }
  return { rows, lanes };
}

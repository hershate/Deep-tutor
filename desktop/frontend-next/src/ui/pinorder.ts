import type { TreeSession } from "../port/hub";

/** A pinned conversation leads its project; the kernel's own order holds within each group. */
export function pinnedFirst(sessions: TreeSession[], pinned: ReadonlySet<string>): TreeSession[] {
  if (pinned.size === 0) return sessions;
  const lead = sessions.filter((session) => pinned.has(session.path));
  return lead.length === 0 || lead.length === sessions.length ? sessions : [...lead, ...sessions.filter((session) => !pinned.has(session.path))];
}

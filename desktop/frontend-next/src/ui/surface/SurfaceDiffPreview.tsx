import { Paper, Stack, Text } from "@mantine/core";
import type { SurfaceDiff } from "../../port/wire";

export function SurfaceDiffPreview({ diff, label }: { diff: SurfaceDiff; label?: string }) {
  const before = diff.before.split("\n");
  const after = diff.after.split("\n");
  let start = 0;
  let end = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start++;
  while (end < before.length - start && end < after.length - start && before[before.length - 1 - end] === after[after.length - 1 - end]) end++;
  const rows = [
    ...after.slice(0, start).map(text => ({ text, sign: " " })),
    ...before.slice(start, before.length - end).map(text => ({ text, sign: "-" })),
    ...after.slice(start, after.length - end).map(text => ({ text, sign: "+" })),
    ...after.slice(after.length - end).map(text => ({ text, sign: " " })),
  ];
  return <Stack gap="xs" className="surface-diff">
    {label && <Text size="sm" fw={500}>{label}</Text>}
    <Paper withBorder p="sm" role="region" aria-label={label}>
      {rows.map((row, i) => <Text className="surface-diff-row" size="sm" ff="monospace" key={i} data-d={row.sign === " " ? undefined : row.sign} c={row.sign === "-" ? "red" : row.sign === "+" ? "teal" : undefined} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
        <span aria-hidden="true">{row.sign} </span><span>{row.text}</span>
      </Text>)}
    </Paper>
  </Stack>;
}

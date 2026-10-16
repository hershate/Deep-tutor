import { useId } from "react";
import { Checkbox, Group as MantineGroup, Paper, Radio, SimpleGrid, Stack, Text, Textarea, TextInput } from "@mantine/core";
import type { ExtensionViewNode } from "../../port/wire";

interface Props {
  node: ExtensionViewNode;
  value: unknown;
  disabled: boolean;
  change: (value: unknown) => void;
}

export function SurfaceField({ node, value, disabled, change }: Props) {
  const f = node.field!;
  const group = useId();
  const input = { label: node.label, "aria-label": node.label, "aria-required": f.required || undefined, required: f.required, readOnly: disabled };
  if (node.kind === "editable_text") return <Textarea data-action="surface.edit" data-target={node.key} {...input} rows={6} value={value === undefined ? "" : String(value)} onChange={e => change(e.target.value)} />;
  if (f.type === "bool") return <Checkbox data-action="surface.edit" data-target={node.key} label={node.label} checked={value === true} disabled={disabled} onChange={e => change(e.target.checked)} />;
  if (f.type !== "choice" && f.type !== "multichoice") return <TextInput data-action="surface.edit" data-target={node.key} {...input} type={f.type === "number" ? "number" : "text"} min={f.min} max={f.max} step="any" value={value === undefined ? "" : String(value)} onChange={e => change(e.target.value)} />;
  const Indicator = f.type === "choice" ? Radio.Indicator : Checkbox.Indicator;
  return <Stack gap="xs">
    <Text size="sm" fw={500}>{node.label}{f.required && " *"}</Text>
    <SimpleGrid cols={node.kind === "choice_card" ? { base: 1, sm: 2 } : 1} spacing="sm" role={f.type === "choice" ? "radiogroup" : "group"} aria-label={node.label} aria-required={f.required || undefined}>
      {f.options?.map(o => {
        const on = f.type === "choice" ? value === o.value : Array.isArray(value) && value.includes(o.value);
        const take = () => {
          if (f.type === "choice") change(on ? undefined : o.value);
          else { const chosen = Array.isArray(value) ? value : []; change(on ? chosen.filter(v => v !== o.value) : [...chosen, o.value]); }
        };
        const content =
          <MantineGroup wrap="nowrap" align="flex-start"><Indicator /><Stack gap="xs" style={{ minWidth: 0 }}>
            <Text size="sm" fw={500}>{o.label}</Text>
            {o.description && <Text size="sm" c="dimmed">{o.description}</Text>}
            {o.preview && <Paper p="sm" withBorder><Text size="sm" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{o.preview}</Text></Paper>}
          </Stack></MantineGroup>
        ;
        return f.type === "choice" ? <Radio.Card data-action="surface.edit" data-target={node.key} key={o.value} name={group} checked={on} disabled={disabled} p="md" onClick={take}>{content}</Radio.Card>
          : <Checkbox.Card data-action="surface.edit" data-target={node.key} key={o.value} checked={on} disabled={disabled} p="md" onClick={take}>{content}</Checkbox.Card>;
      })}
    </SimpleGrid>
  </Stack>;
}

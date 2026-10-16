import { MessageProcessor, type SurfaceModel } from "@a2ui/web_core/v0_9";
import type { ReactCatalogComponent } from "@a2ui/react/v0_9";
import type { ExtensionViewNode } from "../../../port/wire";
import { validSurfaceValue } from "../spec";
import { studioCatalog } from "./catalog";

const components = { text: "Text", markdown: "Markdown", row: "Row", stack: "Stack", kv: "KV", meter: "Meter", pip: "Pip", divider: "Divider", button: "ExtensionButton", field: "Field", choice_card: "Field", editable_text: "Field", diff_preview: "DiffPreview", map: "Field", comparison: "Field", dashboard: "Dashboard" };

export function createSurfaceRuntime(body: ExtensionViewNode[], restored?: Record<string, unknown>) {
  const fields: { node: ExtensionViewNode; id: string }[] = [];
  const nodes: Record<string, unknown>[] = [];
  let serial = 0;
  const walk = (tree: ExtensionViewNode[]): string[] => tree.flatMap(node => {
    const component = Object.hasOwn(components, node.kind) ? components[node.kind] : undefined;
    if (!component) return [];
    const id = `n${serial++}`;
    let props: Record<string, unknown>;
    if (component === "Row" || component === "Stack") props = { children: walk(node.children ?? []) };
    else if (component === "Field" && node.field) {
      const fieldId = `f${fields.length}`;
      fields.push({ node, id: fieldId });
      props = { node, value: { path: `/fields/${fieldId}` } };
    } else if (node.dataset) props = { dataset: node.dataset, label: node.label };
    else if (node.diff) props = { ...node.diff, label: node.label };
    else props = displayProps(node);
    if (component === "ExtensionButton") props = { label: node.label ?? "", actionId: node.actionId ?? "" };
    if (component === "Text" || component === "Markdown" || component === "KV") props.value ??= "";
    nodes.push({ id, component, ...props });
    return [id];
  });
  const children = walk(body);
  nodes.push({ id: "root", component: "Stack", children });
  const initial: Record<string, unknown> = {};
  for (const f of fields) {
    const value = restored ? Object.hasOwn(restored, f.node.key!) ? restored[f.node.key!] : undefined : f.node.field!.default;
    if (value !== undefined) initial[f.id] = value;
  }
  const processor = new MessageProcessor([studioCatalog]);
  processor.processMessages([
    { version: "v0.9", createSurface: { surfaceId: "studio", catalogId: studioCatalog.id } },
    { version: "v0.9", updateComponents: { surfaceId: "studio", components: nodes } },
    { version: "v0.9", updateDataModel: { surfaceId: "studio", path: "/", value: { fields: initial } } },
  ]);
  const surface = processor.model.surfacesMap.get("studio")! as SurfaceModel<ReactCatalogComponent>;
  return {
    surface,
    dispose: () => processor.dispose(),
    setAnswers(values: Record<string, unknown>) {
      const data: Record<string, unknown> = {};
      for (const f of fields) if (Object.hasOwn(values, f.node.key!)) data[f.id] = values[f.node.key!];
      surface.dataModel.set("/fields", data);
    },
    answers() {
      const values: Record<string, unknown> = Object.create(null);
      const data = surface.dataModel.get<Record<string, unknown>>("/fields") ?? {};
      let valid = true;
      for (const { node, id } of fields) {
        let value = Object.hasOwn(data, id) ? data[id] : undefined;
        if (node.field!.type === "number") value = value === "" || value === undefined ? undefined : Number(value);
        if (value === undefined) { if (node.field!.required) valid = false; }
        else { values[node.key!] = value; if (!validSurfaceValue(node.field!, value)) valid = false; }
      }
      return { values, valid };
    },
  };
}

export type SurfaceRuntime = ReturnType<typeof createSurfaceRuntime>;

function displayProps(node: ExtensionViewNode): Record<string, unknown> {
  switch (node.kind) {
    case "text": case "markdown": return { value: node.value ?? "", tone: node.tone };
    case "kv": return { key: node.key ?? "", value: node.value ?? "", tone: node.tone };
    case "meter": return { label: node.label, progress: node.progress ?? 0, tone: node.tone };
    case "pip": return { tone: node.tone };
    default: return {};
  }
}

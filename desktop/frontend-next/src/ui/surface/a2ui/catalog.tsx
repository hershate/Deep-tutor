import { Button } from "@mantine/core";
import { createContext, useContext } from "react";
import { Catalog, CommonSchemas } from "@a2ui/web_core/v0_9";
import { createComponentImplementation, type ReactComponentImplementation } from "@a2ui/react/v0_9";
import { z } from "zod";
import type { ExtensionViewNode, SurfaceDataset } from "../../../port/wire";
import { SurfaceField } from "../SurfaceField";
import { SurfaceDiffPreview } from "../SurfaceDiffPreview";

import { lazySurface } from "./lazy";

const MapPlanner = lazySurface(() => import("../rich/MapPlanner"));
const Comparison = lazySurface(() => import("../rich/Comparison"));
const Dashboard = lazySurface(() => import("../rich/Dashboard"));

export const SurfaceContext = createContext<{ disabled: boolean; inputs: boolean; actionDisabled?: boolean; onAction?: (id: string) => void }>({ disabled: true, inputs: false });

const tone = z.string().optional();
const textSchema = z.object({ value: z.string(), tone }).strict();
const fieldValue = z.union([CommonSchemas.DynamicString, CommonSchemas.DynamicNumber, CommonSchemas.DynamicBoolean, z.array(z.string())]);
const implementations: ReactComponentImplementation[] = [
  createComponentImplementation({ name: "Dashboard", schema: z.object({ dataset: z.custom<SurfaceDataset>(), label: z.string().optional() }).strict() }, ({ props }) => <Dashboard dataset={props.dataset} label={props.label} />),
  createComponentImplementation({ name: "Text", schema: textSchema }, ({ props }) => <span className="xv-t" data-tone={props.tone}>{props.value}</span>),
  createComponentImplementation({ name: "Markdown", schema: textSchema }, ({ props }) => <p className="xv-md">{props.value}</p>),
  ...(["Row", "Stack"] as const).map(name => createComponentImplementation({ name, schema: z.object({ children: CommonSchemas.ChildList }).strict() }, ({ props, buildChild }) =>
    <div className={name === "Row" ? "xv-row" : "xv-stack"}>{props.children.map(child => typeof child === "string" ? buildChild(child) : buildChild(child.id, child.basePath))}</div>)),
  createComponentImplementation({ name: "KV", schema: z.object({ key: z.string(), value: z.string(), tone }).strict() }, ({ props }) =>
    <div className="xv-kv"><span className="k">{props.key}</span><span className="v">{props.value}</span></div>),
  createComponentImplementation({ name: "Meter", schema: z.object({ label: z.string().optional(), progress: z.number(), tone }).strict() }, ({ props }) =>
    <div className="xv-meter" role="progressbar" aria-valuenow={Math.round(props.progress * 100)}><i style={{ width: `${Math.round(Math.max(0, Math.min(1, props.progress)) * 100)}%` }} />{props.label && <span className="lb">{props.label}</span>}</div>),
  createComponentImplementation({ name: "Pip", schema: z.object({ tone }).strict() }, ({ props }) => <i className="pip" data-tone={props.tone} />),
  createComponentImplementation({ name: "Divider", schema: z.object({}).strict() }, () => <hr className="xv-hr" />),
  createComponentImplementation({ name: "ExtensionButton", schema: z.object({ label: z.string(), actionId: z.string() }).strict() }, ({ props }) => {
    const { onAction, inputs, disabled, actionDisabled } = useContext(SurfaceContext);
    if (inputs) return <Button data-action="surface.action" data-target={props.actionId} disabled={disabled||actionDisabled} onClick={()=>onAction?.(props.actionId)}>{props.label}</Button>;
    return <button className="act" data-action="extensions.invoke" onClick={() => onAction?.(props.actionId)}>{props.label}</button>;
  }),
  createComponentImplementation({ name: "Field", schema: z.object({ node: z.custom<ExtensionViewNode>(), value: fieldValue }).strict() }, ({ props }) => {
    const { disabled, inputs } = useContext(SurfaceContext);
    if (inputs && (props.node.kind === "map" || props.node.kind === "comparison")) {
      const View = props.node.kind === "map" ? MapPlanner : Comparison;
      return <View node={props.node} value={props.value} disabled={disabled} change={value => props.setValue(value)} />;
    }
    return inputs ? <SurfaceField node={props.node} value={props.value} disabled={disabled} change={value => props.setValue(value as string | number | boolean | string[])} /> : null;
  }),
  createComponentImplementation({ name: "DiffPreview", schema: z.object({ before: z.string(), after: z.string(), label: z.string().optional() }).strict() }, ({ props }) =>
    <SurfaceDiffPreview diff={{ before: props.before, after: props.after }} label={props.label} />),
];

export const studioCatalog = new Catalog("reasonix:studio:surface:v1", "v0.9", implementations, []);

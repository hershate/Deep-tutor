import { useSyncExternalStore, type ReactNode } from "react";
import { MantineProvider, createTheme, type MantineColorSchemeManager, type CSSVariablesResolver } from "@mantine/core";
import "@mantine/core/styles.layer.css";

const theme = createTheme({ primaryColor: "gray", fontFamily: "var(--ui)", fontFamilyMonospace: "var(--mono)", defaultRadius: "md" });
const variables: CSSVariablesResolver = () => ({ variables: {}, light: { "--mantine-color-body": "var(--surface)", "--mantine-color-text": "var(--text)", "--mantine-color-dimmed": "var(--muted)", "--mantine-primary-color-filled": "var(--text)", "--mantine-primary-color-filled-hover": "color-mix(in srgb, var(--text) 85%, var(--surface))" }, dark: { "--mantine-color-body": "var(--surface)", "--mantine-color-text": "var(--text)", "--mantine-color-dimmed": "var(--muted)" } });
const root = () => undefined;
const manager: MantineColorSchemeManager = { get: fallback => fallback, set() {}, subscribe() {}, unsubscribe() {}, clear() {} };
function subscribe(change: () => void) {
  const observer = new MutationObserver(change);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}
const appearance = () => document.documentElement.dataset.theme === "dark" ? "dark" as const : "light" as const;

export function SurfaceKit({ children }: { children: ReactNode }) {
  const scheme = useSyncExternalStore(subscribe, appearance);
  return <MantineProvider theme={theme} colorSchemeManager={manager} forceColorScheme={scheme} getRootElement={root} cssVariablesSelector=".surface-kit" cssVariablesResolver={variables} withGlobalClasses={false}>
    <div className="surface-kit" data-mantine-color-scheme={scheme}>{children}</div>
  </MantineProvider>;
}

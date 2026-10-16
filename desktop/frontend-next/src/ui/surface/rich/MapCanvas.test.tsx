// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SurfaceKit } from "../SurfaceKit";
import MapCanvas from "./MapCanvas";

const api = vi.hoisted(() => ({ fitBounds: vi.fn(), jumpTo: vi.fn() }));
vi.mock("maplibre-gl", () => ({ setWorkerUrl: vi.fn() }));
vi.mock("react-map-gl/maplibre", async () => {
  const React = await import("react");
  return {
    default: React.forwardRef(({ children, onLoad }: { children: React.ReactNode; onLoad: () => void }, ref) => {
      React.useImperativeHandle(ref, () => api);
      React.useEffect(onLoad, []);
      return <div>{children}</div>;
    }),
    Marker: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Source: () => null, Layer: () => null, NavigationControl: () => null
  };
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("starts with the whole itinerary and restores it after explicit place focus", () => {
  const places = [{ value: "a", label: "Park", location: { latitude: 35, longitude: 139 } }, { value: "b", label: "Museum", location: { latitude: 36, longitude: 140 } }];
  const focus = vi.fn();
  const view = (version: number) => <SurfaceKit><MapCanvas places={places} order={["a", "b"]} active="b" focusVersion={version} focus={focus} /></SurfaceKit>;
  const mounted = render(view(0));
  expect(api.fitBounds).toHaveBeenCalledWith([[139, 35], [140, 36]], expect.objectContaining({ duration: 0 }));
  expect(api.jumpTo).not.toHaveBeenCalled();
  mounted.rerender(view(1));
  expect(api.jumpTo).toHaveBeenCalledWith({ center: [140, 36] });
  api.fitBounds.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "查看全部地点" }));
  expect(api.fitBounds).toHaveBeenCalledWith([[139, 35], [140, 36]], expect.objectContaining({ maxZoom: 14 }));
  expect(focus).not.toHaveBeenCalled();
});

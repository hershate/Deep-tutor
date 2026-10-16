// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SurfaceKit } from "./SurfaceKit";
import { OptionImage } from "./media";
import { SurfaceMediaContext } from "./media-context";
import { SurfaceCard, surfaceTool } from "./SurfaceCard";
import type { Item } from "../../state/session";
import type { ExtensionViewNode } from "../../port/wire";

vi.mock("./rich/MapCanvas", () => ({ default: ({ active }: { active?: string }) => <div data-testid="map-active">{active}</div> }));
vi.mock("@mantine/charts", () => ({ AreaChart: () => <div data-testid="area" />, BarChart: () => <div data-testid="bar" /> }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const places = [{ value: "park", label: "Park", group: "Day 1", location: { latitude: 35, longitude: 139 } }, { value: "museum", label: "Museum", group: "Day 2", location: { latitude: 36, longitude: 140 } }];
const map: ExtensionViewNode = { kind: "map", key: "trip", label: "Tokyo", field: { type: "multichoice", required: true, default: ["park", "museum"], options: places } };
const compare: ExtensionViewNode = { kind: "comparison", key: "camera", label: "Cameras", field: { type: "choice", options: [{ value: "a", label: "Alpha", group: "Travel", price: 100, currency: "USD", facts: [{ label: "Weight", value: "300 g" }] }, { value: "b", label: "Beta", group: "Studio", price: 200, currency: "USD", facts: [{ label: "Weight", value: "500 g" }] }] } };
function item(body: ExtensionViewNode[]): Extract<Item, { t: "ask" }> { return { t: "ask", id: "row", ask: { id: "ask", questions: [{ id: "surface", prompt: "Plan", options: [], surface: { spec_version: 1, body } }] } }; }

it("links place focus, preserves reordered ids across a failed send and seals replay", async () => {
  const answer = vi.fn().mockRejectedValueOnce(new Error("refused")).mockResolvedValueOnce(undefined);
  const view = render(<SurfaceCard item={item([map])} onAnswer={answer} />);
  fireEvent.click(await screen.findByRole("button", { name: "Museum" }));
  expect(screen.getByTestId("map-active").textContent).toBe("museum");
  fireEvent.click(screen.getByRole("button", { name: "上移 Museum" }));
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await screen.findByRole("alert");
  expect(answer.mock.calls[0][2][0].value.trip).toEqual(["museum", "park"]);
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalledTimes(2));
  expect(answer.mock.calls[1][2][0].value.trip).toEqual(["museum", "park"]);
  view.rerender(<SurfaceCard item={{ ...item([map]), answered: [[]], surfaceValues: { trip: ["museum", "park"] } }} onAnswer={answer} />);
  await waitFor(() => expect((screen.getByRole("button", { name: "上移 Park" }) as HTMLButtonElement).disabled).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Park" }));
  expect(screen.getByTestId("map-active").textContent).toBe("park");
});

it("filters comparison options without dropping the selection, exposes facts and submits ids", async () => {
  const answer = vi.fn(async () => {});
  render(<SurfaceCard item={item([compare])} onAnswer={answer} />);
  fireEvent.click(await screen.findByRole("button", { name: "选择 Alpha" }));
  fireEvent.change(screen.getByLabelText("搜索选项"), { target: { value: "Beta" } });
  expect(screen.queryByRole("button", { name: "选择 Alpha" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "属性对比" }));
  expect(screen.getByRole("table").textContent).toContain("500 g");
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalled());
  expect((answer.mock.calls as unknown[][])[0][2]).toEqual([{ questionId: "surface", selected: [], value: { camera: "a" } }]);
});

it("recomputes dashboard summaries and details from the chosen series and range", async () => {
  const dashboard: ExtensionViewNode = { kind: "dashboard", label: "Activity", dataset: { categories: ["Jan", "Feb", "Mar", "Apr"], series: [{ id: "sales", label: "Sales", values: [1, 2, 3, 4] }, { id: "orders", label: "Orders", values: [10, 20, 30, 40] }] } };
  render(<SurfaceCard item={item([dashboard, compare])} onAnswer={vi.fn(async () => {})} />);
  fireEvent.click(await screen.findByRole("button", { name: "Orders" }));
  fireEvent.click(screen.getByRole("button", { name: "最近一半" }));
  expect(screen.getByTestId("dashboard-total").textContent).toBe("70");
  fireEvent.click(screen.getByRole("button", { name: "柱状图" }));
  expect(screen.getByTestId("bar")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "数据明细" }));
  expect(screen.getByRole("table").textContent).toContain("Mar");
  expect(screen.getByRole("table").textContent).not.toContain("Jan");
});


it("compares only differing properties and retains selection across workspace expansion", async () => {
  const options = compare.field!.options!.map(o => ({ ...o, facts: [...o.facts!, { label: "Warranty", value: "2 years" }] }));
  render(<SurfaceCard item={item([{ ...compare, field: { ...compare.field!, options } }])} onAnswer={vi.fn(async () => {})} />);
  fireEvent.click(await screen.findByRole("button", { name: "选择 Alpha" }));
  fireEvent.click(screen.getByRole("button", { name: "属性对比" }));
  fireEvent.click(screen.getByRole("button", { name: "仅看差异" }));
  expect(screen.getByRole("table").textContent).toContain("Weight");
  expect(screen.getByRole("table").textContent).not.toContain("Warranty");
  fireEvent.click(screen.getByRole("button", { name: "展开工作区" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "选择 Alpha" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "仅看差异" }));
  expect(screen.getByRole("table").textContent).toContain("Warranty");
});


it("reveals a focused stop within the itinerary without scrolling on replay", async () => {
  const view = render(<SurfaceCard item={{ ...item([map]), answered: [[]], surfaceValues: { trip: ["park", "museum"] } }} onAnswer={vi.fn(async () => {})} />);
  const museum = await screen.findByRole("button", { name: "Museum" });
  const list = view.container.querySelector(".rich-map-list") as HTMLElement;
  const row = museum.closest(".rich-stop") as HTMLElement;
  vi.spyOn(list, "getBoundingClientRect").mockReturnValue({ top: 100, bottom: 200 } as DOMRect);
  vi.spyOn(row, "getBoundingClientRect").mockReturnValue({ top: 300, bottom: 380 } as DOMRect);
  expect(list.scrollTop).toBe(0);
  fireEvent.click(museum);
  expect(list.scrollTop).toBe(180);
  expect(row.dataset.active).toBe("true");
});


it("loads product media through the owning workspace and preserves selection after image failure", async () => {
  const body = [{ ...compare, field: { ...compare.field!, options: [{ ...compare.field!.options![0], image: { path: "images/camera.png", alt: "Camera front", source: "https://example.com/camera" } }] } }];
  const port = { workspaceImageURL: vi.fn((path: string) => `/workspace-a/image?path=${encodeURIComponent(path)}`) };
  const answer = vi.fn(async () => {});
  const view = render(<SurfaceCard item={item(body)} port={port} onAnswer={answer} />);
  const photo = await screen.findByRole("img", { name: "Camera front" });
  expect(photo.getAttribute("src")).toBe("/workspace-a/image?path=images%2Fcamera.png");
  fireEvent.click(screen.getByRole("button", { name: "选择 Alpha" }));
  expect(screen.getByRole("link", { name: /example.com/ }).getAttribute("href")).toBe("https://example.com/camera");
  fireEvent.error(photo);
  expect(screen.queryByRole("img")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "提交" }));
  await waitFor(() => expect(answer).toHaveBeenCalled());
  expect((answer.mock.calls as unknown[][])[0][2]).toEqual([{ questionId: "surface", selected: [], value: { camera: "a" } }]);
  view.rerender(<SurfaceCard item={item(body)} port={{ workspaceImageURL: () => "/workspace-b/image" }} onAnswer={answer} />);
  expect((await screen.findByRole("img", { name: "Camera front" })).getAttribute("src")).toBe("/workspace-b/image");
});


it("uses the same workspace media port for nonblocking product actions", async () => {
  const spec = { spec_version: 1, body: [{ ...compare, field: { ...compare.field!, options: [{ ...compare.field!.options![0], image: { path: "images/camera.png", alt: "Action camera" } }] } }, { kind: "button", label: "Continue", actionId: "continue" }] };
  const port = {
    workspaceImageURL: (path: string) => `/runtime/action-image?path=${encodeURIComponent(path)}`,
    surfaceActionState: vi.fn(async () => ({ active: true, action: { sessionId: "s", surfaceId: "surface", callId: "call", requestId: "", actionId: "", values: {} } })),
    surfaceAction: vi.fn(), cancelQueued: vi.fn()
  };
  render(<>{surfaceTool({ id: "call", name: "render_surface", readOnly: true, args: JSON.stringify(spec), output: JSON.stringify({ nonblocking: true }) }, false, port)}</>);
  expect((await screen.findByRole("img", { name: "Action camera" })).getAttribute("src")).toBe("/runtime/action-image?path=images%2Fcamera.png");
  expect(port.surfaceAction).not.toHaveBeenCalled();
});

it("keeps photos usable when a plugin supplies a malformed source link", () => {
  for (const source of ["not a URL", "javascript:alert(1)", "https://user:password@example.com/photo"]) {
    const view = render(<SurfaceKit><SurfaceMediaContext.Provider value={{ workspaceImageURL: () => "/workspace/image" }}><OptionImage image={{ path: "photo.png", alt: "Photo", source }} /></SurfaceMediaContext.Provider></SurfaceKit>);
    expect(screen.getByRole("img", { name: "Photo" })).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    view.unmount();
  }
});

it("opens photos, retries failures and closes the viewer without changing answers", async () => {
 const photo = { path: "images/camera.png", alt: "Camera front", source: "https://example.com/camera" };
 const body = [{ ...compare, field: { ...compare.field!, options: [{ ...compare.field!.options![0], image: photo }] } }];
 const answer = vi.fn(async () => {});
 render(<SurfaceCard item={item(body)} port={{ workspaceImageURL: () => "/workspace/photo" }} onAnswer={answer} />);
 fireEvent.click(await screen.findByRole("button", { name: "选择 Alpha" }));
 fireEvent.click(screen.getByRole("button", { name: "展开工作区" }));
 fireEvent.click(screen.getByRole("button", { name: "查看大图：Camera front" }));
 const viewer = await screen.findByRole("dialog", { name: "Camera front" });
 const image = within(viewer).getByRole("img", { name: "Camera front" });
 expect(image.getAttribute("src")).toBe("/workspace/photo");
 expect(within(viewer).getByRole("link", { name: /example.com/ })).toBeTruthy();
 fireEvent.error(image);
 fireEvent.click(within(viewer).getByRole("button", { name: "重试图片" }));
 expect(within(viewer).getByRole("img", { name: "Camera front" })).toBeTruthy();
 fireEvent.keyDown(viewer, { key: "Escape" });
 await waitFor(() => expect(screen.queryByRole("dialog", { name: "Camera front" })).toBeNull());
 expect(screen.getByRole("dialog", { name: "Cameras" })).toBeTruthy();
 expect(screen.getByRole("button", { name: "选择 Alpha" }).getAttribute("aria-pressed")).toBe("true");
 expect(answer).not.toHaveBeenCalled();
});

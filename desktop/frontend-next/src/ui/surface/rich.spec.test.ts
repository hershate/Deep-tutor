import { expect, it } from "vitest";
import { parseSurface } from "./spec";

const map = { kind: "map", key: "stops", label: "Trip", field: { type: "multichoice", options: [{ value: "a", label: "Park", location: { latitude: 35, longitude: 139 } }] } };
const dashboard = { kind: "dashboard", label: "Revenue", dataset: { categories: ["Jan", "Feb"], series: [{ id: "sales", label: "Sales", values: [10, 20] }] } };
it("accepts mapped choices and rectangular dashboard data", () => {
  expect(parseSurface({ spec_version: 1, body: [map, dashboard] })).not.toBeNull();
  const comparison = { ...map, kind: "comparison", field: { type: "choice", options: [{ value: "camera", label: "Camera", price: 100, currency: "USD", facts: [{ label: "Weight", value: "300 g" }] }] } };
  expect(parseSurface({ spec_version: 1, body: [comparison] })).not.toBeNull();
});
it("refuses malformed coordinates, executable properties and inconsistent series", () => {
  for (const location of [{ latitude: 91, longitude: 0 }, { latitude: 0, longitude: -181 }, { longitude: 0 }, { latitude: 0, longitude: 0, url: "https://invalid.test" }]) {
    expect(parseSurface({ spec_version: 1, body: [{ ...map, field: { ...map.field, options: [{ ...map.field.options[0], location }] } }] })).toBeNull();
  }
  expect(parseSurface({ spec_version: 1, body: [{ ...dashboard, dataset: { ...dashboard.dataset, series: [{ id: "sales", label: "Sales", values: [1] }] } }] })).toBeNull();
});

it("accepts workspace image references and refuses network or escaping paths", () => {
  const spec = (path: string) => ({ spec_version: 1, body: [{ ...map, field: { ...map.field, options: [{ ...map.field.options[0], image: { path, alt: "Park" } }] } }] });
  expect(parseSurface(spec("images/park.png"))).not.toBeNull();
  for (const path of ["../park.png", "/park.png", "https://example.com/a.png", "C:/a.png", "images/../a.png", "images//a.png", "a.svg", "a.png?x=1", "%2e%2e/a.png"]) expect(parseSurface(spec(path))).toBeNull();
});

it("accepts source links but refuses active schemes and credentials", () => {
  const spec = (source: string) => ({ spec_version: 1, body: [{ ...map, field: { ...map.field, options: [{ ...map.field.options[0], image: { path: "images/park.png", alt: "Park", source } }] } }] });
  expect(parseSurface(spec("https://example.com/park"))).not.toBeNull();
  for (const source of ["javascript:alert(1)", "file:///tmp/a", "//example.com/a", "https://user:pass@example.com/a"]) expect(parseSurface(spec(source))).toBeNull();
});

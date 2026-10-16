// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SurfaceCard } from "./SurfaceCard";
import type { Item } from "../../state/session";

afterEach(() => { cleanup(); delete document.documentElement.dataset.theme; });
it("follows desktop appearance without writing a separate global theme", async () => {
  document.documentElement.dataset.theme = "dark";
  const item: Extract<Item, {t:"ask"}> = {t:"ask", id:"theme", ask:{id:"theme", questions:[{id:"surface",prompt:"Name",options:[],surface:{spec_version:1,body:[{kind:"field",key:"name",label:"Name",field:{type:"string"}}]}}]}};
  const view = render(<SurfaceCard item={item} onAnswer={vi.fn(async () => {})} />);
  await screen.findByLabelText("Name");
  expect(view.container.querySelector('.surface-kit')?.getAttribute('data-mantine-color-scheme')).toBe("dark");
  expect(document.documentElement.hasAttribute('data-mantine-color-scheme')).toBe(false);
  document.documentElement.dataset.theme = "light";
  await waitFor(() => expect(view.container.querySelector('.surface-kit')?.getAttribute('data-mantine-color-scheme')).toBe("light"));
});

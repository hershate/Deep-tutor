// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Markdown } from "./Markdown";
import { conversationFile } from "./filelinks";

afterEach(cleanup);

it.each([
  ["docs/report.md", "docs/report.md"], ["./docs/report.md", "docs/report.md"],
  ["/work/docs/report.md", "docs/report.md"], ["file:///work/docs/report%20one.md", "docs/report one.md"],
  ["docs/report.md#heading", "docs/report.md"], ["../secret.txt", null],
  ["/workspace/report.md", null], ["https://example.com/report.md", null],
  ["javascript:alert(1)", null], ["#heading", null], ["//host/share/report.md", null],
  ["/work/../secret.txt", null], ["file://host/work/report.md", null],
])("resolves %s inside the conversation's workspace", (href, expected) => {
  expect(conversationFile("/work", href)).toBe(expected);
});

it("resolves Windows drive paths without treating web schemes or other drives as files", () => {
  expect(conversationFile("C:\\Work", "c:\\work\\docs\\report.md")).toBe("docs/report.md");
  expect(conversationFile("C:\\Work", "file:///C:/Work/docs/report.md")).toBe("docs/report.md");
  expect(conversationFile("C:\\Work", "D:\\Work\\report.md")).toBeNull();
});

it.each(["fmt.Println", "e.g.", "report.pdf"])("leaves the bare code span %s as code", (value) => {
  const path = (href: string) => conversationFile("/work", href);
  const local = { path, link: (href: string) => path(href) ? vi.fn() : null, image: () => null };
  const { container, getByText } = render(<Markdown local={local} text={`\`${value}\``} />);
  expect(getByText(value).tagName).toBe("CODE");
  expect(container.querySelector("a")).toBeNull();
});

it.each([
  ["/work", "./report.pdf", "report.pdf"],
  ["/work", "docs/report.md", "docs/report.md"],
  ["C:\\Work", "docs\\report.pdf", "docs/report.pdf"],
])("links the code span %s %s", (root, value, expected) => {
  const opened = vi.fn();
  const path = (href: string) => conversationFile(root, href);
  const local = { path, link: (href: string) => path(href) ? () => opened(path(href)) : null, image: () => null };
  const { getByText } = render(<Markdown local={local} text={`\`${value}\``} />);
  fireEvent.click(getByText(value));
  expect(opened).toHaveBeenCalledWith(expected);
});

it("links file references in prose and code spans while preserving web links, labels and code listings", () => {
  const opened = vi.fn(), menu = vi.fn();
  const path = (href: string) => conversationFile("/work", href);
  const local = { path, link: (href: string) => path(href) ? () => opened(path(href)) : null, image: () => null, menu };
  const { container, getByText } = render(<Markdown local={local} text={'[Report](/work/docs/report.md)\n\n`/work/docs/report one.md`\n\nCreated /work/docs/result.txt。\n\n[web](https://example.com)\n\n```sh\n/work/docs/listing.sh\n```'} />);
  fireEvent.click(getByText("Report"));
  fireEvent.click(getByText("/work/docs/report one.md"));
  fireEvent.click(getByText("/work/docs/result.txt"));
  expect(opened.mock.calls).toEqual([["docs/report.md"], ["docs/report one.md"], ["docs/result.txt"]]);
  expect(getByText("web").getAttribute("target")).toBe("_blank");
  expect(container.querySelector("pre a")).toBeNull();
  fireEvent.contextMenu(getByText("Report"));
  expect(menu).toHaveBeenCalledOnce();
});

import { expect, it } from "vitest";
import { systemLabel } from "./osname";

it("names Windows 11 from the build number and leaves every other system as reported", () => {
  expect(systemLabel("windows", "10.0.26200", "amd64")).toBe("Windows 11 (10.0.26200) amd64");
  expect(systemLabel("windows", "10.0.22000", "arm64")).toBe("Windows 11 (10.0.22000) arm64");
  expect(systemLabel("windows", "10.0.19045", "amd64")).toBe("windows 10.0.19045 amd64");
  expect(systemLabel("windows", "10.0.21999", "amd64")).toBe("windows 10.0.21999 amd64");
  expect(systemLabel("windows", "6.3.9600", "amd64")).toBe("windows 6.3.9600 amd64");
  expect(systemLabel("darwin", "15.1", "arm64")).toBe("darwin 15.1 arm64");
  expect(systemLabel("windows", "", "amd64")).toBe("windows amd64");
});

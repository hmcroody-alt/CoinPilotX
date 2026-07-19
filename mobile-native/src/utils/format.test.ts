import { compactPreview, formatFileSize } from "./format";

describe("formatFileSize", () => {
  it("returns empty string for falsy input", () => {
    expect(formatFileSize(undefined)).toBe("");
    expect(formatFileSize(0)).toBe("");
  });

  it("formats bytes below 1KB", () => {
    expect(formatFileSize(512)).toBe("512 B");
  });

  it("formats kilobytes", () => {
    expect(formatFileSize(2048)).toBe("2 KB");
  });

  it("formats megabytes", () => {
    expect(formatFileSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("compactPreview", () => {
  it("returns fallback for empty input", () => {
    expect(compactPreview("", "none")).toBe("none");
  });

  it("collapses whitespace", () => {
    expect(compactPreview("hello   world\n\nfoo")).toBe("hello world foo");
  });

  it("truncates long text with ellipsis", () => {
    const long = "a".repeat(120);
    const result = compactPreview(long);
    expect(result.length).toBe(96);
    expect(result.endsWith("...")).toBe(true);
  });
});

import { parseBlocks, parseInline } from "../utils/markdown";

describe("parseInline", () => {
  it("splits bold, inline code and https links; leaves the rest as text", () => {
    expect(parseInline("a **b** `c` [d](https://e.io) f")).toEqual([
      { text: "a " },
      { text: "b", bold: true },
      { text: " " },
      { text: "c", code: true },
      { text: " " },
      { text: "d", href: "https://e.io" },
      { text: " f" },
    ]);
  });

  it("never turns non-http links or html into anything special", () => {
    expect(parseInline("[x](javascript:alert(1)) <b>hi</b>")).toEqual([
      { text: "[x](javascript:alert(1)) <b>hi</b>" },
    ]);
  });
});

describe("parseBlocks", () => {
  it("handles paragraphs, headings, lists and rules", () => {
    const blocks = parseBlocks("## Title\n\nline one\nline two\n\n- a\n- b\n\n3. c\n4. d\n\n---");
    expect(blocks.map((b) => b.type)).toEqual(["h", "p", "ul", "ol", "hr"]);
    expect((blocks[1] as any).spans[0].text).toBe("line one\nline two");
    expect((blocks[2] as any).items).toHaveLength(2);
    expect((blocks[3] as any).start).toBe(3);
  });

  it("keeps numbering when blank lines split an ordered list", () => {
    const blocks = parseBlocks("1. a\n\n2. b");
    expect(blocks.map((b) => (b as any).start)).toEqual([1, 2]);
  });

  it("parses fenced code, including an unclosed fence while streaming", () => {
    const done = parseBlocks("x\n```py\nprint(1)\n```\ny");
    expect(done.map((b) => b.type)).toEqual(["p", "code", "p"]);
    expect((done[1] as any).lang).toBe("py");
    const open = parseBlocks("```\nstill typing");
    expect(open).toEqual([{ type: "code", text: "still typing", lang: "" }]);
  });

  it("appends indented continuation lines to the previous list item", () => {
    const [b] = parseBlocks("- first\n  more\n- second");
    expect((b as any).items[0][0].text).toBe("first\nmore");
  });

  it("returns no blocks for empty input", () => {
    expect(parseBlocks("")).toEqual([]);
  });
});

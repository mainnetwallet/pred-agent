import { createSseParser } from "../services/sse";

describe("createSseParser", () => {
  it("emits each complete frame once, even when fed in arbitrary chunks", () => {
    const got: Array<[string, any]> = [];
    const p = createSseParser((e, d) => got.push([e, d]));
    const full =
      'event: session\ndata: {"session_id":"s1"}\n\n' +
      'event: delta\ndata: {"text":"হ্যালো"}\n\n' +
      'event: done\ndata: {"reply":"হ্যালো","cards":[]}\n\n';
    for (let i = 5; i < full.length + 5; i += 5) p.feed(full.slice(0, Math.min(i, full.length)));
    expect(got.map((g) => g[0])).toEqual(["session", "delta", "done"]);
    expect(got[1][1].text).toBe("হ্যালো");
  });

  it("ignores partial frames and malformed JSON", () => {
    const got: string[] = [];
    const p = createSseParser((e) => got.push(e));
    p.feed('event: delta\ndata: {"te');
    expect(got).toEqual([]);
    p.feed('event: delta\ndata: {"te\n\nevent: reset\ndata: {}\n\n');
    expect(got).toEqual(["reset"]);
  });
});

// Tiny, dependency-free markdown subset for chat replies (ChatGPT-style rendering).
// Supports: paragraphs, #/##/### headings, -/*/• and 1. lists, ``` code fences,
// --- rules, **bold**, `inline code` and [text](https://link). Everything else is
// shown as plain text, so a model reply can never inject markup or run anything.

export interface Span {
  text: string;
  bold?: boolean;
  code?: boolean;
  href?: string;
}

export type Block =
  | { type: "p"; spans: Span[] }
  | { type: "h"; level: 1 | 2 | 3; spans: Span[] }
  | { type: "ul"; items: Span[][] }
  | { type: "ol"; start: number; items: Span[][] }
  | { type: "code"; text: string; lang: string }
  | { type: "hr" };

const INLINE_RE = /`([^`\n]+)`|\*\*([^*\n]+?)\*\*|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;

export function parseInline(src: string): Span[] {
  const out: Span[] = [];
  const re = new RegExp(INLINE_RE.source, "g");
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    if (m.index > last) out.push({ text: src.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ text: m[1], code: true });
    else if (m[2] !== undefined) out.push({ text: m[2], bold: true });
    else out.push({ text: m[3], href: m[4] });
    last = m.index + m[0].length;
  }
  if (last < src.length) out.push({ text: src.slice(last) });
  return out;
}

interface OpenList {
  type: "ul" | "ol";
  start: number;
  raw: string[];
}

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: OpenList | null = null;

  const flushPara = () => {
    if (para.length) {
      blocks.push({ type: "p", spans: parseInline(para.join("\n")) });
      para = [];
    }
  };
  const flushList = () => {
    if (!list) return;
    const items = list.raw.map(parseInline);
    blocks.push(list.type === "ol" ? { type: "ol", start: list.start, items } : { type: "ul", items });
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const fence = /^\s*```\s*([\w+-]*)\s*$/.exec(line);
    if (fence) {
      flushPara();
      flushList();
      const body: string[] = [];
      i++;
      // An unclosed fence (reply still streaming) swallows the rest as code.
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
        body.push(lines[i]);
        i++;
      }
      blocks.push({ type: "code", text: body.join("\n"), lang: fence[1] });
      continue;
    }

    if (/^\s*$/.test(line)) {
      flushPara();
      flushList();
      continue;
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushPara();
      flushList();
      blocks.push({ type: "hr" });
      continue;
    }

    const h = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (h) {
      flushPara();
      flushList();
      blocks.push({ type: "h", level: Math.min(3, h[1].length) as 1 | 2 | 3, spans: parseInline(h[2]) });
      continue;
    }

    const ul = /^\s*[-*•]\s+(.*)$/.exec(line);
    const ol = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
    if (ul || ol) {
      flushPara();
      const type = ul ? "ul" : "ol";
      if (list && list.type !== type) flushList();
      if (!list) list = { type, start: ol ? parseInt(ol[1], 10) : 1, raw: [] };
      list.raw.push((ul ? ul[1] : ol![2]).trim());
      continue;
    }

    if (list && /^\s+\S/.test(line)) {
      // indented continuation of the previous list item
      list.raw[list.raw.length - 1] += "\n" + line.trim();
      continue;
    }

    flushList();
    para.push(line);
  }
  flushPara();
  flushList();
  return blocks;
}

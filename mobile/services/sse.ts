// Minimal incremental Server-Sent Events parser (no dependencies).
// Feed it the *full* response text so far; it emits each completed frame once.

export type SseHandler = (event: string, data: any) => void;

export function createSseParser(onEvent: SseHandler) {
  let consumed = 0;
  return {
    feed(fullText: string) {
      let buf = fullText.slice(consumed);
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        consumed += idx + 2;
        buf = buf.slice(idx + 2);
        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          // ignore malformed frame
        }
      }
    },
  };
}

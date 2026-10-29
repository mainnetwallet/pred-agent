// Renders a chat reply (markdown subset) as native Text — no HTML, nothing executable.

import React, { useMemo } from "react";
import { Linking, ScrollView, StyleProp, StyleSheet, Text, TextStyle, View } from "react-native";
import { Block, Span, parseBlocks } from "../utils/markdown";
import { Colors, MONO, radius, useThemedStyles } from "../utils/theme";

function Inline({ spans, style }: { spans: Span[]; style?: StyleProp<TextStyle> }) {
  const styles = useThemedStyles(makeStyles);
  return (
    <Text style={[styles.text, style]} selectable>
      {spans.map((s, i) => (
        <Text
          key={i}
          style={s.code ? styles.inlineCode : s.bold ? styles.bold : s.href ? styles.link : undefined}
          onPress={s.href ? () => { Linking.openURL(s.href!).catch(() => undefined); } : undefined}
        >
          {s.text}
        </Text>
      ))}
    </Text>
  );
}

function BlockView({ block }: { block: Block }) {
  const styles = useThemedStyles(makeStyles);
  switch (block.type) {
    case "p":
      return <Inline spans={block.spans} />;
    case "h":
      return <Inline spans={block.spans} style={block.level === 1 ? styles.h1 : block.level === 2 ? styles.h2 : styles.h3} />;
    case "ul":
    case "ol":
      return (
        <View style={styles.list}>
          {block.items.map((item, i) => (
            <View key={i} style={styles.li}>
              <Text style={styles.marker}>{block.type === "ol" ? `${block.start + i}.` : "•"}</Text>
              <View style={styles.liBody}>
                <Inline spans={item} />
              </View>
            </View>
          ))}
        </View>
      );
    case "code":
      return (
        <View style={styles.codeBox}>
          {!!block.lang && <Text style={styles.codeLang}>{block.lang}</Text>}
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <Text style={styles.codeText} selectable>{block.text}</Text>
          </ScrollView>
        </View>
      );
    case "hr":
      return <View style={styles.hr} />;
    default:
      return null;
  }
}

export const Markdown: React.FC<{ text: string }> = ({ text }) => {
  const styles = useThemedStyles(makeStyles);
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return (
    <View style={styles.root}>
      {blocks.map((b, i) => (
        <BlockView key={i} block={b} />
      ))}
    </View>
  );
};

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    root: { gap: 10 },
    text: { color: c.text, fontSize: 16, lineHeight: 24 },
    bold: { fontWeight: "700" },
    link: { color: c.primary, textDecorationLine: "underline" },
    inlineCode: { fontFamily: MONO, fontSize: 14, backgroundColor: c.surfaceAlt },
    h1: { fontSize: 22, lineHeight: 28, fontWeight: "800", marginTop: 4 },
    h2: { fontSize: 19, lineHeight: 26, fontWeight: "800", marginTop: 4 },
    h3: { fontSize: 16, lineHeight: 24, fontWeight: "800", marginTop: 2 },
    list: { gap: 4 },
    li: { flexDirection: "row", gap: 8 },
    marker: { color: c.textSecondary, fontSize: 16, lineHeight: 24, minWidth: 18, textAlign: "right" },
    liBody: { flex: 1 },
    codeBox: { backgroundColor: c.surfaceAlt, borderRadius: radius.md, borderWidth: 1, borderColor: c.border, padding: 12, gap: 6 },
    codeLang: { color: c.textMuted, fontSize: 11, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.6 },
    codeText: { color: c.text, fontFamily: MONO, fontSize: 13, lineHeight: 19 },
    hr: { height: 1, backgroundColor: c.border, marginVertical: 4 },
  });

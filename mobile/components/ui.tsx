// Shared UI kit for the redesigned app: icons, header, cards, pills, segmented control.
// Icons are drawn with react-native-svg (already a dependency) so no icon font is needed.

import React from "react";
import { View, Text, TouchableOpacity, StyleSheet, ViewStyle } from "react-native";
import Svg, { Path, Circle, Rect } from "react-native-svg";
import { useNavigation } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Colors, MONO, radius, useTheme, useThemedStyles } from "../utils/theme";
import { formatUsd } from "../utils/format";

// ── Icons ────────────────────────────────────────────────────

export type IconName =
  | "home"
  | "live"
  | "chat"
  | "position"
  | "settings"
  | "panic"
  | "back"
  | "chevron"
  | "refresh"
  | "trash"
  | "logout"
  | "send"
  | "compose";

export const Icon: React.FC<{ name: IconName; size?: number; color: string; strokeWidth?: number }> = ({
  name,
  size = 22,
  color,
  strokeWidth = 1.8,
}) => {
  const p = { stroke: color, strokeWidth, fill: "none", strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      {name === "home" && (
        <>
          <Path d="M3.5 11 12 4l8.5 7" {...p} />
          <Path d="M5.5 9.5V20h13V9.5" {...p} />
          <Path d="M10 20v-5.5h4V20" {...p} />
        </>
      )}
      {name === "live" && (
        <>
          <Circle cx="12" cy="12" r="2.2" {...p} />
          <Path d="M7.6 7.6a6.2 6.2 0 0 0 0 8.8M16.4 7.6a6.2 6.2 0 0 1 0 8.8" {...p} />
          <Path d="M4.8 4.8a10.2 10.2 0 0 0 0 14.4M19.2 4.8a10.2 10.2 0 0 1 0 14.4" {...p} />
        </>
      )}
      {name === "chat" && (
        <>
          <Path d="M4 5.5h16v10.5H10l-4.5 3.5V16H4z" {...p} />
          <Path d="M8 9.5h8M8 12.5h5" {...p} />
        </>
      )}
      {name === "position" && (
        <>
          <Path d="M4 20V4" {...p} />
          <Path d="M4 20h16" {...p} />
          <Path d="m7.5 15 3.5-4 3 2.5 5-6" {...p} />
          <Path d="M16 7.5h3.5V11" {...p} />
        </>
      )}
      {name === "settings" && (
        <>
          <Path d="M4 7h9M17 7h3M4 17h3M11 17h9" {...p} />
          <Circle cx="15" cy="7" r="2" {...p} />
          <Circle cx="9" cy="17" r="2" {...p} />
        </>
      )}
      {name === "panic" && (
        <>
          <Path d="M12 3.5 21 19.5H3z" {...p} />
          <Path d="M12 10v4.2" {...p} />
          <Circle cx="12" cy="16.9" r="0.6" fill={color} stroke={color} strokeWidth={1} />
        </>
      )}
      {name === "back" && <Path d="m14.5 5.5-6.5 6.5 6.5 6.5" {...p} />}
      {name === "chevron" && <Path d="m9.5 5.5 6.5 6.5-6.5 6.5" {...p} />}
      {name === "refresh" && (
        <>
          <Path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" {...p} />
          <Path d="M19.5 4.5v4h-4" {...p} />
        </>
      )}
      {name === "trash" && (
        <>
          <Path d="M4.5 7h15M9.5 7V4.5h5V7" {...p} />
          <Rect x="6.5" y="7" width="11" height="13" rx="1.5" {...p} />
        </>
      )}
      {name === "send" && (
        <>
          <Path d="M12 19V5" {...p} />
          <Path d="m5.5 11.5 6.5-6.5 6.5 6.5" {...p} />
        </>
      )}
      {name === "compose" && (
        <>
          <Path d="M12 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19h11a1.5 1.5 0 0 0 1.5-1.5V12" {...p} />
          <Path d="m17.5 4.5 2 2L11 15l-3 1 1-3z" {...p} />
        </>
      )}
      {name === "logout" && (
        <>
          <Path d="M10 4.5H5.5v15H10" {...p} />
          <Path d="M14 8.5 18 12l-4 3.5M18 12H9.5" {...p} />
        </>
      )}
    </Svg>
  );
};

// ── Mode pill ────────────────────────────────────────────────

export const ModePill: React.FC<{ mode: string | null; paused: boolean }> = ({ mode, paused }) => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  if (!mode) return null;
  const live = mode === "LIVE";
  const bg = paused ? c.pausedBg : live ? c.liveBg : c.paperBg;
  const fg = paused ? c.pausedBorder : live ? c.liveBorder : c.paperBorder;
  const label = paused ? `PAUSED · ${live ? "LIVE" : "PAPER"}` : live ? "LIVE · REAL FUNDS" : "PAPER";
  return (
    <View style={[styles.pill, { backgroundColor: bg, borderColor: fg }]}>
      <View style={[styles.pillDot, { backgroundColor: fg }]} />
      <Text style={[styles.pillText, { color: fg }]}>{label}</Text>
    </View>
  );
};

// ── Screen header ────────────────────────────────────────────

interface ScreenHeaderProps {
  title: string;
  subtitle?: string;
  mode?: string | null;
  paused?: boolean;
  onBack?: () => void;
  right?: React.ReactNode;
  hidePanic?: boolean;
}

export const ScreenHeader: React.FC<ScreenHeaderProps> = ({
  title,
  subtitle,
  mode = null,
  paused = false,
  onBack,
  right,
  hidePanic,
}) => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<any>();
  return (
    <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
      <View style={styles.headerRow}>
        {onBack && (
          <TouchableOpacity onPress={onBack} style={styles.iconBtn} accessibilityLabel="Back" hitSlop={8}>
            <Icon name="back" color={c.text} />
          </TouchableOpacity>
        )}
        <View style={styles.headerTitleWrap}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {title}
          </Text>
          {subtitle ? (
            <Text style={styles.headerSub} numberOfLines={1}>
              {subtitle}
            </Text>
          ) : null}
        </View>
        {right}
        {!hidePanic && (
          <TouchableOpacity
            onPress={() => navigation.navigate("Panic")}
            style={[styles.iconBtn, styles.panicBtn]}
            accessibilityLabel="Panic"
            hitSlop={8}
          >
            <Icon name="panic" color={c.danger} size={20} />
          </TouchableOpacity>
        )}
      </View>
      {mode ? (
        <View style={styles.pillRow}>
          <ModePill mode={mode} paused={paused} />
        </View>
      ) : null}
    </View>
  );
};

// ── Card / section / empty / stat ────────────────────────────

export const Card: React.FC<{ children: React.ReactNode; style?: ViewStyle }> = ({ children, style }) => {
  const styles = useThemedStyles(makeStyles);
  return <View style={[styles.card, style]}>{children}</View>;
};

export const SectionTitle: React.FC<{ title: string; action?: string; onAction?: () => void }> = ({
  title,
  action,
  onAction,
}) => {
  const styles = useThemedStyles(makeStyles);
  return (
    <View style={styles.sectionRow}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {action ? (
        <TouchableOpacity onPress={onAction} hitSlop={8}>
          <Text style={styles.sectionAction}>{action}</Text>
        </TouchableOpacity>
      ) : null}
    </View>
  );
};

export const EmptyState: React.FC<{ title: string; hint?: string }> = ({ title, hint }) => {
  const styles = useThemedStyles(makeStyles);
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>{title}</Text>
      {hint ? <Text style={styles.emptyHint}>{hint}</Text> : null}
    </View>
  );
};

export const Stat: React.FC<{ label: string; value: string; tone?: "long" | "short" | "text" }> = ({
  label,
  value,
  tone = "text",
}) => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const color = tone === "long" ? c.long : tone === "short" ? c.short : c.text;
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, { color }]} numberOfLines={1} adjustsFontSizeToFit>
        {value}
      </Text>
    </View>
  );
};

// Signed money with arrow so colour is never the only signal.
export function signedUsd(value: string | number): { text: string; tone: "long" | "short" | "text" } {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(n) || n === 0) return { text: formatUsd(0), tone: "text" };
  return n > 0
    ? { text: `▲ +${formatUsd(n)}`, tone: "long" }
    : { text: `▼ −${formatUsd(Math.abs(n))}`, tone: "short" };
}

// ── Chips ────────────────────────────────────────────────────

export const SideChip: React.FC<{ side: string }> = ({ side }) => {
  const c = useTheme();
  const long = side.toLowerCase() === "long";
  const color = long ? c.long : c.short;
  return (
    <View style={[chipBase, { backgroundColor: long ? c.longSoft : c.shortSoft, borderColor: color }]}>
      <Text style={[chipText, { color }]}>
        {long ? "▲ LONG" : "▼ SHORT"}
      </Text>
    </View>
  );
};

export const StateChip: React.FC<{ label: string; tone?: "long" | "short" | "alert" | "neutral" | "muted" }> = ({
  label,
  tone = "muted",
}) => {
  const c = useTheme();
  const map = {
    long: [c.long, c.longSoft],
    short: [c.short, c.shortSoft],
    alert: [c.alert, c.alertSoft],
    neutral: [c.primary, c.primarySoft],
    muted: [c.textSecondary, c.surfaceAlt],
  } as const;
  const [fg, bg] = map[tone];
  return (
    <View style={[chipBase, { backgroundColor: bg, borderColor: fg }]}>
      <Text style={[chipText, { color: fg }]}>{label}</Text>
    </View>
  );
};

const chipBase: ViewStyle = {
  paddingHorizontal: 8,
  paddingVertical: 3,
  borderRadius: radius.sm,
  borderWidth: StyleSheet.hairlineWidth * 2,
  alignSelf: "flex-start",
};
const chipText = { fontSize: 11, fontWeight: "700" as const, letterSpacing: 0.4 };

// ── Segmented control ────────────────────────────────────────

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
}) {
  const styles = useThemedStyles(makeStyles);
  return (
    <View style={styles.segment}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <TouchableOpacity
            key={o.value}
            style={[styles.segmentItem, active && styles.segmentItemActive]}
            onPress={() => onChange(o.value)}
            accessibilityRole="button"
            accessibilityState={{ selected: active }}
          >
            <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{o.label}</Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

// ── Styles ───────────────────────────────────────────────────

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    pill: {
      flexDirection: "row",
      alignItems: "center",
      alignSelf: "flex-start",
      paddingHorizontal: 10,
      paddingVertical: 4,
      borderRadius: radius.pill,
      borderWidth: 1,
      gap: 6,
    },
    pillDot: { width: 7, height: 7, borderRadius: 4 },
    pillText: { fontSize: 11, fontWeight: "800", letterSpacing: 0.6 },

    header: {
      backgroundColor: c.background,
      paddingHorizontal: 16,
      paddingBottom: 10,
    },
    headerRow: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 44 },
    headerTitleWrap: { flex: 1 },
    headerTitle: { color: c.text, fontSize: 26, fontWeight: "800", letterSpacing: -0.5 },
    headerSub: { color: c.textSecondary, fontSize: 12, marginTop: 1 },
    iconBtn: {
      width: 40,
      height: 40,
      borderRadius: radius.pill,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: c.surface,
      borderWidth: 1,
      borderColor: c.border,
    },
    panicBtn: { borderColor: c.danger, backgroundColor: c.shortSoft },
    pillRow: { marginTop: 8 },

    card: {
      backgroundColor: c.surface,
      borderRadius: radius.lg,
      padding: 16,
      borderWidth: 1,
      borderColor: c.border,
    },
    sectionRow: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "baseline",
      marginTop: 20,
      marginBottom: 10,
    },
    sectionTitle: {
      color: c.textSecondary,
      fontSize: 12,
      fontWeight: "800",
      letterSpacing: 1,
      textTransform: "uppercase",
    },
    sectionAction: { color: c.primary, fontSize: 13, fontWeight: "700" },
    empty: { alignItems: "center", paddingVertical: 36, paddingHorizontal: 24 },
    emptyTitle: { color: c.text, fontSize: 15, fontWeight: "700", textAlign: "center" },
    emptyHint: { color: c.textSecondary, fontSize: 13, textAlign: "center", marginTop: 6, lineHeight: 19 },

    stat: { flex: 1 },
    statLabel: { color: c.textSecondary, fontSize: 11, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase" },
    statValue: { fontFamily: MONO, fontSize: 17, fontWeight: "700", marginTop: 4 },

    segment: {
      flexDirection: "row",
      backgroundColor: c.surfaceAlt,
      borderRadius: radius.md,
      padding: 3,
    },
    segmentItem: { flex: 1, paddingVertical: 9, borderRadius: radius.md - 3, alignItems: "center" },
    segmentItemActive: { backgroundColor: c.surface, borderWidth: 1, borderColor: c.border },
    segmentText: { color: c.textSecondary, fontSize: 13, fontWeight: "700" },
    segmentTextActive: { color: c.text },
  });

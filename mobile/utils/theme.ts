// Theme — follows the system light/dark setting (palette from DESIGN.md).

import { useMemo } from "react";
import { useColorScheme } from "react-native";

export interface Colors {
  isDark: boolean;
  background: string;
  surface: string;
  surfaceAlt: string; // small buttons, chips
  text: string;
  textSecondary: string;
  textMuted: string;
  border: string;
  primary: string;
  inputBackground: string;
  placeholder: string;
  long: string;
  short: string;
  neutral: string;
  alert: string;
  danger: string;
  dangerDark: string;
  track: string; // gauge bar track
  marker: string; // gauge market tick
  onAccent: string; // text on filled accent buttons
  paperBg: string;
  paperBorder: string;
  liveBg: string;
  liveBorder: string;
  pausedBg: string;
  pausedBorder: string;
  shadow: string;
}

export const lightColors: Colors = {
  isDark: false,
  background: "#F4F4F4",
  surface: "#FFFFFF",
  surfaceAlt: "#F5F5F5",
  text: "#1A1F26",
  textSecondary: "#666666",
  textMuted: "#999999",
  border: "#DDDDDD",
  primary: "#007AFF",
  inputBackground: "#FFFFFF",
  placeholder: "#999999",
  long: "#22C55E",
  short: "#EF4444",
  neutral: "#2563EB",
  alert: "#D97706",
  danger: "#DC2626",
  dangerDark: "#B91C1C",
  track: "#E0E0E0",
  marker: "#666666",
  onAccent: "#FFFFFF",
  paperBg: "#E3F2FD",
  paperBorder: "#1565C0",
  liveBg: "#FFEBEE",
  liveBorder: "#C62828",
  pausedBg: "#FFF3E0",
  pausedBorder: "#EF6C00",
  shadow: "#000000",
};

export const darkColors: Colors = {
  isDark: true,
  background: "#0B0F14",
  surface: "#141B23",
  surfaceAlt: "#1C2530",
  text: "#E8ECF1",
  textSecondary: "#8FA3B8",
  textMuted: "#6B7C8F",
  border: "#2A3644",
  primary: "#60A5FA",
  inputBackground: "#10161D",
  placeholder: "#6B7C8F",
  long: "#22C55E",
  short: "#EF4444",
  neutral: "#60A5FA",
  alert: "#F59E0B",
  danger: "#EF4444",
  dangerDark: "#DC2626",
  track: "#2A3644",
  marker: "#8FA3B8",
  onAccent: "#FFFFFF",
  paperBg: "#10243A",
  paperBorder: "#60A5FA",
  liveBg: "#3A1316",
  liveBorder: "#EF4444",
  pausedBg: "#3A2A10",
  pausedBorder: "#F59E0B",
  shadow: "#000000",
};

export function useTheme(): Colors {
  const scheme = useColorScheme();
  return scheme === "dark" ? darkColors : lightColors;
}

// Build a StyleSheet once per theme: const styles = useThemedStyles(makeStyles)
export function useThemedStyles<T>(factory: (c: Colors) => T): T {
  const c = useTheme();
  return useMemo(() => factory(c), [c]); // eslint-disable-line react-hooks/exhaustive-deps
}

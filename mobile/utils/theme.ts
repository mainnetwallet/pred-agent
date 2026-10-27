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
  background: "#E4E8EC",
  surface: "#F2F4F6",
  surfaceAlt: "#DDE2E7",
  text: "#1A1F26",
  textSecondary: "#56606B",
  textMuted: "#7A8590",
  border: "#C5CCD4",
  primary: "#0A6BE0",
  inputBackground: "#F2F4F6",
  placeholder: "#7A8590",
  long: "#16A34A",
  short: "#DC2626",
  neutral: "#2563EB",
  alert: "#C2410C",
  danger: "#C62828",
  dangerDark: "#B91C1C",
  track: "#CBD2D9",
  marker: "#56606B",
  onAccent: "#FFFFFF",
  paperBg: "#D3E3F3",
  paperBorder: "#1565C0",
  liveBg: "#F1D6D9",
  liveBorder: "#C62828",
  pausedBg: "#F1E0C8",
  pausedBorder: "#C2410C",
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

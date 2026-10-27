// Reusable components

import React from "react";
import { View, Text, StyleSheet, ActivityIndicator } from "react-native";
import { formatUsd, isLiveMode } from "../utils/format";
import { Colors, useTheme, useThemedStyles } from "../utils/theme";

export const StatusBanner: React.FC<{
  mode: string | null;
  paused: boolean;
}> = ({ mode, paused }) => {
  const styles = useThemedStyles(makeStyles);
  const live = isLiveMode(mode);

  if (!mode) {
    return (
      <View style={styles.container}>
        <Text style={styles.label}>Loading...</Text>
      </View>
    );
  }

  return (
    <View style={[styles.container, live ? styles.live : styles.paper, paused && styles.paused]}>
      <Text style={[styles.label, paused && styles.pausedLabel]}>
        {paused ? "PAUSED — " : ""}{live ? "LIVE — real funds" : "PAPER — fake funds"}
      </Text>
    </View>
  );
};

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    container: {
      flexDirection: "row",
      alignItems: "center",
      paddingHorizontal: 16,
      paddingVertical: 10,
      borderBottomWidth: 1,
      borderBottomColor: c.border,
    },
    paper: {
      backgroundColor: c.paperBg,
      borderBottomColor: c.paperBorder,
    },
    live: {
      backgroundColor: c.liveBg,
      borderBottomColor: c.liveBorder,
    },
    paused: {
      backgroundColor: c.pausedBg,
      borderBottomColor: c.pausedBorder,
    },
    label: {
      fontWeight: "600",
      fontSize: 14,
      color: c.text,
    },
    pausedLabel: {
      color: c.pausedBorder,
    },
  });

export const MoneyDisplay: React.FC<{
  amount: string | number;
  label?: string;
}> = ({ amount, label }) => {
  const styles = useThemedStyles(makeMoneyStyles);
  return (
    <View style={styles.moneyContainer}>
      {label && <Text style={styles.moneyLabel}>{label}</Text>}
      <Text style={styles.moneyValue}>{formatUsd(amount)}</Text>
    </View>
  );
};

const makeMoneyStyles = (c: Colors) =>
  StyleSheet.create({
    moneyContainer: {
      flexDirection: "row",
      alignItems: "baseline",
    },
    moneyLabel: {
      fontSize: 12,
      color: c.textSecondary,
      marginRight: 4,
    },
    moneyValue: {
      fontFamily: "monospace",
      fontSize: 16,
      fontWeight: "600",
      color: c.text,
    },
  });

export const LoadingSpinner: React.FC = () => {
  const c = useTheme();
  return (
    <View style={spinnerStyles.spinnerContainer}>
      <ActivityIndicator size="large" color={c.textSecondary} />
    </View>
  );
};

const spinnerStyles = StyleSheet.create({
  spinnerContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
});

export const OfflineBadge: React.FC<{
  stale: boolean;
  lastUpdate: string | null;
}> = ({ stale, lastUpdate }) => {
  const styles = useThemedStyles(makeOfflineStyles);
  if (!stale && !lastUpdate) return null;
  return (
    <View style={styles.offlineContainer}>
      <Text style={styles.offlineText}>
        {stale ? "STALE — " : ""}Last update: {lastUpdate || "—"}
      </Text>
    </View>
  );
};

const makeOfflineStyles = (c: Colors) =>
  StyleSheet.create({
    offlineContainer: {
      paddingHorizontal: 12,
      paddingVertical: 6,
      backgroundColor: c.pausedBg,
      alignSelf: "flex-start",
      borderRadius: 4,
    },
    offlineText: {
      fontSize: 11,
      color: c.pausedBorder,
      fontWeight: "600",
    },
  });

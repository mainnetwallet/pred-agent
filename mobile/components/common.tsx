// Reusable components

import React from "react";
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from "react-native";
import { formatUsd, formatPriceCents, formatEdge, isLiveMode, isPaperMode } from "../utils/format";

export const StatusBanner: React.FC<{
  mode: string | null;
  paused: boolean;
}> = ({ mode, paused }) => {
  const live = isLiveMode(mode);
  const paper = isPaperMode(mode);

  if (!mode) {
    return (
      <View style={styles.container}>
        <Text style={styles.text}>Loading...</Text>
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

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
  },
  paper: {
    backgroundColor: "#E3F2FD",
    borderBottomColor: "#1565C0",
  },
  live: {
    backgroundColor: "#FFEBEE",
    borderBottomColor: "#C62828",
  },
  paused: {
    backgroundColor: "#FFF3E0",
    borderBottomColor: "#EF6C00",
  },
  label: {
    fontWeight: "600",
    fontSize: 14,
  },
  pausedLabel: {
    color: "#EF6C00",
  },
});

export const MoneyDisplay: React.FC<{
  amount: string | number;
  label?: string;
}> = ({ amount, label }) => {
  return (
    <View style={styles.moneyContainer}>
      {label && <Text style={styles.moneyLabel}>{label}</Text>}
      <Text style={styles.moneyValue}>{formatUsd(amount)}</Text>
    </View>
  );
};

const moneyStyles = StyleSheet.create({
  moneyContainer: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  moneyLabel: {
    fontSize: 12,
    color: "#666",
    marginRight: 4,
  },
  moneyValue: {
    fontFamily: "monospace",
    fontSize: 16,
    fontWeight: "600",
  },
});

export const LoadingSpinner: React.FC = () => {
  return (
    <View style={styles.spinnerContainer}>
      <ActivityIndicator size="large" color="#666" />
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
  if (!stale && !lastUpdate) return null;
  return (
    <View style={styles.offlineContainer}>
      <Text style={styles.offlineText}>
        {stale ? "STALE — " : ""}Last update: {lastUpdate || "—"}
      </Text>
    </View>
  );
};

const offlineStyles = StyleSheet.create({
  offlineContainer: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: "#FFF3E0",
    alignSelf: "flex-start",
    borderRadius: 4,
  },
  offlineText: {
    fontSize: 11,
    color: "#EF6C00",
    fontWeight: "600",
  },
});

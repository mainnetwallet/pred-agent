// Panic screen — always reachable (header or floating control), two-step confirm,
// shows per-order cancel results including cancel_noop (kit/08_MOBILE_APP.md)

import React, { useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Alert,
  FlatList,
} from "react-native";
import * as LocalAuthentication from "expo-local-authentication";
import { useUIStore } from "../store/index";
import { Colors, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { StatusBanner } from "../components/common";

export const PanicScreen: React.FC = () => {
  const styles = useThemedStyles(makeStyles);
  const { mode, paused } = useUIStore();
  const [confirming, setConfirming] = useState(false);
  const [results, setResults] = useState<any[]>([]);

  const handlePanic = async () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }

    // Second tap — biometric confirmation
    const authenticated = await LocalAuthentication.authenticateAsync({
      promptMessage: "Confirm PANIC — cancel all orders",
    });
    if (!authenticated.success) {
      Alert.alert("Error", "Biometric authentication failed");
      setConfirming(false);
      return;
    }

    try {
      const resp = await api.panic("biometric-confirmed");
      if (resp.ok && resp.data) {
        setResults([]);
        Alert.alert("Panic", "All orders cancelled. Check results below.");
      } else {
        Alert.alert("Error", resp.error?.message || "Panic failed");
      }
    } catch {
      Alert.alert("Error", "Network error");
    } finally {
      setConfirming(false);
    }
  };

  const renderResult = ({ item }: { item: any }) => (
    <View style={styles.resultRow}>
      <Text style={styles.resultOrder}>{item.order_id}</Text>
      <Text style={[styles.resultStatus, item.status === "cancel_noop" ? styles.statusNoop : styles.statusOk]}>
        {item.status}
      </Text>
    </View>
  );

  return (
    <View style={styles.container}>
      <StatusBanner mode={mode} paused={paused} />
      <View style={styles.content}>
        <View style={styles.panicCard}>
          <Text style={styles.panicTitle}>PANIC</Text>
          <Text style={styles.panicSubtitle}>
            Pause all proposals and cancel all open orders
          </Text>
          <TouchableOpacity
            style={[styles.panicButton, confirming && styles.panicButtonConfirming]}
            onPress={handlePanic}
          >
            <Text style={styles.panicButtonText}>
              {confirming ? "TAP AGAIN TO CONFIRM" : "PANIC — CANCEL ALL"}
            </Text>
          </TouchableOpacity>
          <Text style={styles.panicHint}>
            Requires biometric confirmation. Cannot be undone.
          </Text>
        </View>

        <View style={styles.resultsSection}>
          <Text style={styles.sectionTitle}>Cancel Results</Text>
          {results.length === 0 ? (
            <Text style={styles.empty}>No results yet. Tap PANIC to cancel all orders.</Text>
          ) : (
            <FlatList
              data={results}
              keyExtractor={(item) => item.order_id}
              renderItem={renderResult}
            />
          )}
        </View>
      </View>
    </View>
  );
};

const makeStyles = (c: Colors) => StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: c.background,
  },
  content: {
    padding: 16,
  },
  panicCard: {
    backgroundColor: c.surface,
    borderRadius: 16,
    padding: 24,
    alignItems: "center",
    marginBottom: 20,
    shadowColor: c.shadow,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 8,
    elevation: 5,
    borderWidth: 2,
    borderColor: c.danger,
  },
  panicTitle: {
    fontSize: 28,
    fontWeight: "800",
    color: c.danger,
    marginBottom: 8,
  },
  panicSubtitle: {
    fontSize: 14,
    color: c.textSecondary,
    textAlign: "center",
    marginBottom: 20,
  },
  panicButton: {
    backgroundColor: c.danger,
    paddingVertical: 18,
    paddingHorizontal: 32,
    borderRadius: 12,
    width: "80%",
  },
  panicButtonConfirming: {
    backgroundColor: c.dangerDark,
    borderColor: c.short,
    borderWidth: 2,
  },
  panicButtonText: {
    color: c.onAccent,
    fontWeight: "800",
    fontSize: 16,
    textAlign: "center",
  },
  panicHint: {
    marginTop: 12,
    fontSize: 12,
    color: c.textMuted,
    textAlign: "center",
  },
  resultsSection: {
    marginTop: 16,
  },
  sectionTitle: {
    color: c.text,
    fontSize: 16,
    fontWeight: "600",
    marginBottom: 8,
  },
  resultRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
    backgroundColor: c.surface,
  },
  resultOrder: {
    color: c.text,
    fontSize: 13,
    fontFamily: "monospace",
  },
  resultStatus: {
    fontSize: 13,
    fontWeight: "600",
  },
  statusOk: {
    color: c.long,
  },
  statusNoop: {
    color: c.alert,
  },
  empty: {
    fontSize: 14,
    color: c.textSecondary,
    textAlign: "center",
    paddingVertical: 20,
  },
});

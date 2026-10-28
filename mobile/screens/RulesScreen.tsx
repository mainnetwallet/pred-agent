/**
 * Exit rules — list, confirm PENDING, cancel ACTIVE/PENDING.
 * Rendered inside the Position tab (RulesPanel); the default export is a standalone wrapper.
 */

import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import * as LocalAuthentication from "expo-local-authentication";
import { api } from "../services/api";
import { Rule, RuleStatus } from "../types/api";
import { Colors, MONO, radius, useTheme, useThemedStyles } from "../utils/theme";
import { EmptyState, ScreenHeader, StateChip } from "../components/ui";

const FILTERS: { label: string; value: RuleStatus | undefined }[] = [
  { label: "All", value: undefined },
  { label: "Active", value: "ACTIVE" },
  { label: "Pending", value: "PENDING" },
  { label: "Done", value: "DONE" },
];

function statusTone(s: string): "long" | "alert" | "short" | "muted" {
  if (s === "ACTIVE") return "long";
  if (s === "PENDING") return "alert";
  if (s === "BLOCKED" || s === "FAILED") return "short";
  return "muted";
}

export function RulesPanel() {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<RuleStatus | undefined>(undefined);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.getRules(filter);
      if (r.ok && r.data) setRules(r.data);
    } catch {
      // keep last list
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  const handleConfirm = (rule: Rule) => {
    Alert.alert(
      "Confirm exit rule",
      `${rule.kind.replace("_", " ")} — ${rule.trigger_type.replace("_", " ")} @ ${rule.trigger_value}\n\nThe monitor will auto-execute this when triggered.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Confirm rule",
          onPress: async () => {
            const auth = await LocalAuthentication.authenticateAsync({ promptMessage: "Confirm exit rule" });
            if (!auth.success) return;
            const r = await api.confirmRule(rule.id, "biometric-confirmed");
            if (r.ok) load();
            else Alert.alert("Error", r.error?.message ?? "Failed to confirm rule.");
          },
        },
      ]
    );
  };

  const handleCancel = (rule: Rule) => {
    Alert.alert("Cancel rule", `Cancel this ${rule.kind.replace("_", " ")} rule?`, [
      { text: "Keep", style: "cancel" },
      {
        text: "Cancel rule",
        style: "destructive",
        onPress: async () => {
          const r = await api.cancelRule(rule.id);
          if (r.ok) load();
          else Alert.alert("Error", r.error?.message ?? "Failed");
        },
      },
    ]);
  };

  return (
    <View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
        {FILTERS.map((f) => {
          const active = filter === f.value;
          return (
            <TouchableOpacity
              key={f.label}
              style={[styles.chip, active && styles.chipActive]}
              onPress={() => setFilter(f.value)}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{f.label}</Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {loading && rules.length === 0 ? (
        <ActivityIndicator style={{ marginTop: 32 }} color={c.textSecondary} />
      ) : rules.length === 0 ? (
        <EmptyState title="No exit rules" hint={'Create one from Chat, e.g. "$5 profit hole sell koro".'} />
      ) : (
        rules.map((rule) => (
          <View key={rule.id} style={styles.card}>
            <View style={styles.cardHead}>
              <Text style={styles.cardTitle}>{rule.kind === "take_profit" ? "Take profit" : "Stop loss"}</Text>
              <StateChip label={rule.status} tone={statusTone(rule.status)} />
            </View>
            <Text style={styles.trigger}>
              {rule.trigger_type.replace("_", " ")} @ {rule.trigger_value}
            </Text>
            <Text style={styles.meta} numberOfLines={1}>
              {rule.market_id}
            </Text>
            <Text style={styles.meta}>
              size {rule.size} · via {rule.created_via} · {rule.mode}
            </Text>
            {rule.last_eval_note ? <Text style={styles.note}>{rule.last_eval_note}</Text> : null}
            <View style={styles.actions}>
              {rule.status === "PENDING" && (
                <TouchableOpacity style={[styles.btn, { backgroundColor: c.long }]} onPress={() => handleConfirm(rule)}>
                  <Text style={styles.btnText}>Confirm</Text>
                </TouchableOpacity>
              )}
              {(rule.status === "PENDING" || rule.status === "ACTIVE") && (
                <TouchableOpacity style={[styles.btn, styles.btnOutline]} onPress={() => handleCancel(rule)}>
                  <Text style={[styles.btnText, { color: c.short }]}>Cancel</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>
        ))
      )}
    </View>
  );
}

export default function RulesScreen({ navigation }: { navigation?: any }) {
  const styles = useThemedStyles(makeStyles);
  return (
    <View style={styles.screen}>
      <ScreenHeader title="Exit rules" onBack={() => navigation?.goBack()} />
      <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 32 }}>
        <RulesPanel />
      </ScrollView>
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    screen: { flex: 1, backgroundColor: c.background },
    filters: { gap: 8, paddingVertical: 12 },
    chip: {
      paddingHorizontal: 14,
      paddingVertical: 7,
      borderRadius: radius.pill,
      borderWidth: 1,
      borderColor: c.border,
      backgroundColor: c.surface,
    },
    chipActive: { backgroundColor: c.primarySoft, borderColor: c.primary },
    chipText: { color: c.textSecondary, fontSize: 13, fontWeight: "700" },
    chipTextActive: { color: c.primary },
    card: {
      backgroundColor: c.surface,
      borderRadius: radius.lg,
      borderWidth: 1,
      borderColor: c.border,
      padding: 14,
      marginBottom: 10,
      gap: 4,
    },
    cardHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
    cardTitle: { color: c.text, fontSize: 15, fontWeight: "800" },
    trigger: { color: c.text, fontFamily: MONO, fontSize: 14, fontWeight: "700", marginTop: 4 },
    meta: { color: c.textSecondary, fontSize: 12 },
    note: { color: c.alert, fontSize: 12, marginTop: 4 },
    actions: { flexDirection: "row", gap: 8, marginTop: 10 },
    btn: { borderRadius: radius.sm, paddingHorizontal: 16, paddingVertical: 9, minHeight: 40, justifyContent: "center" },
    btnOutline: { borderWidth: 1, borderColor: c.short },
    btnText: { color: c.onAccent, fontSize: 13, fontWeight: "800" },
  });

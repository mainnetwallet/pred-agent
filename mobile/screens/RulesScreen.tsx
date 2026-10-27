/**
 * RulesScreen — view, confirm, and cancel exit rules.
 * Phase 8 (D2): list ACTIVE/PENDING rules, confirm PENDING, cancel ACTIVE/PENDING.
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { api } from "../services/api";
import { Rule, RuleStatus } from "../types/api";

const STATUS_COLORS: Record<string, string> = {
  ACTIVE:    "#2fbf71",
  PENDING:   "#e5a93d",
  DONE:      "#8a90a0",
  CANCELLED: "#8a90a0",
  EXPIRED:   "#8a90a0",
  BLOCKED:   "#e5484d",
  FAILED:    "#e5484d",
};

const FILTERS: { label: string; value: RuleStatus | undefined }[] = [
  { label: "All", value: undefined },
  { label: "Active", value: "ACTIVE" },
  { label: "Pending", value: "PENDING" },
  { label: "Done", value: "DONE" },
];

function RuleCard({ rule, onConfirm, onCancel }: { rule: Rule; onConfirm: () => void; onCancel: () => void }) {
  const statusColor = STATUS_COLORS[rule.status] ?? "#8a90a0";
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle} numberOfLines={1}>
          {rule.kind.replace("_", " ").toUpperCase()} — {rule.trigger_type.replace("_", " ")} @ {rule.trigger_value}
        </Text>
        <Text style={[styles.statusBadge, { color: statusColor }]}>{rule.status}</Text>
      </View>
      <Text style={styles.meta}>market: {rule.market_id}</Text>
      <Text style={styles.meta}>size: {rule.size} · via: {rule.created_via} · mode: {rule.mode}</Text>
      {rule.last_eval_note ? <Text style={styles.note}>{rule.last_eval_note}</Text> : null}
      {rule.confirmed_at ? <Text style={styles.meta}>confirmed: {new Date(rule.confirmed_at).toLocaleString()}</Text> : null}
      {rule.triggered_at ? <Text style={styles.meta}>triggered: {new Date(rule.triggered_at).toLocaleString()}</Text> : null}
      <View style={styles.actions}>
        {rule.status === "PENDING" && (
          <TouchableOpacity style={[styles.btn, styles.btnOk]} onPress={onConfirm}>
            <Text style={styles.btnText}>✓ Confirm</Text>
          </TouchableOpacity>
        )}
        {(rule.status === "PENDING" || rule.status === "ACTIVE") && (
          <TouchableOpacity style={[styles.btn, styles.btnBad]} onPress={onCancel}>
            <Text style={styles.btnText}>✗ Cancel</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

export default function RulesScreen() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<RuleStatus | undefined>(undefined);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.getRules(filter);
      if (r.ok && r.data) setRules(r.data);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const handleConfirm = useCallback((rule: Rule) => {
    Alert.alert(
      "Confirm Exit Rule",
      `Confirm: ${rule.kind} — ${rule.trigger_type} @ ${rule.trigger_value}?\n\nThe monitor will auto-execute this when triggered.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Confirm Rule",
          onPress: async () => {
            const r = await api.confirmRule(rule.id);
            if (r.ok) {
              Alert.alert("✓ Confirmed", "Rule is now ACTIVE.");
              load();
            } else {
              Alert.alert("Error", r.error?.message ?? "Failed to confirm rule.");
            }
          },
        },
      ]
    );
  }, [load]);

  const handleCancel = useCallback((rule: Rule) => {
    Alert.alert(
      "Cancel Rule",
      `Cancel this ${rule.kind} rule?`,
      [
        { text: "Keep", style: "cancel" },
        {
          text: "Cancel Rule",
          style: "destructive",
          onPress: async () => {
            const r = await api.cancelRule(rule.id);
            if (r.ok) load();
            else Alert.alert("Error", r.error?.message ?? "Failed");
          },
        },
      ]
    );
  }, [load]);

  return (
    <View style={styles.container}>
      {/* Filter chips */}
      <View style={styles.filters}>
        {FILTERS.map((f) => (
          <TouchableOpacity
            key={f.label}
            style={[styles.filterChip, filter === f.value && styles.filterChipActive]}
            onPress={() => setFilter(f.value)}
          >
            <Text style={[styles.filterText, filter === f.value && styles.filterTextActive]}>
              {f.label}
            </Text>
          </TouchableOpacity>
        ))}
        <TouchableOpacity style={styles.refreshBtn} onPress={load}>
          <Text style={styles.filterText}>↻</Text>
        </TouchableOpacity>
      </View>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color="#4c8dff" />
      ) : (
        <FlatList
          data={rules}
          keyExtractor={(r) => r.id}
          renderItem={({ item }) => (
            <RuleCard
              rule={item}
              onConfirm={() => handleConfirm(item)}
              onCancel={() => handleCancel(item)}
            />
          )}
          contentContainerStyle={styles.listContent}
          ListEmptyComponent={
            <Text style={styles.empty}>
              No rules yet. Use Chat to create exit rules:{"\n"}e.g. "$5 profit hole sell koro"
            </Text>
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0f1115" },
  filters: { flexDirection: "row", padding: 12, gap: 8, flexWrap: "wrap" },
  filterChip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, borderWidth: 1, borderColor: "#272b35", backgroundColor: "#181b22" },
  filterChipActive: { backgroundColor: "#4c8dff", borderColor: "#4c8dff" },
  filterText: { color: "#8a90a0", fontSize: 13 },
  filterTextActive: { color: "#fff" },
  refreshBtn: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, borderWidth: 1, borderColor: "#272b35" },
  listContent: { padding: 12, gap: 10, paddingBottom: 24 },
  card: { backgroundColor: "#181b22", borderRadius: 12, padding: 14, borderWidth: 1, borderColor: "#272b35" },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 6 },
  cardTitle: { color: "#e8eaf0", fontWeight: "600", fontSize: 14, flex: 1, marginRight: 8 },
  statusBadge: { fontSize: 11, fontWeight: "700" },
  meta: { color: "#8a90a0", fontSize: 12, marginTop: 2 },
  note: { color: "#e5a93d", fontSize: 12, marginTop: 4 },
  actions: { flexDirection: "row", gap: 8, marginTop: 10 },
  btn: { borderRadius: 8, paddingHorizontal: 14, paddingVertical: 7 },
  btnOk: { backgroundColor: "#2fbf71" },
  btnBad: { backgroundColor: "#e5484d" },
  btnText: { color: "#fff", fontSize: 13, fontWeight: "600" },
  empty: { color: "#8a90a0", textAlign: "center", marginTop: 60, lineHeight: 24, fontSize: 14 },
});

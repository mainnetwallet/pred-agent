// Position — open positions (with Close), orders (with Cancel) and exit rules in one tab.

import React, { useCallback, useState } from "react";
import { View, Text, ScrollView, StyleSheet, TouchableOpacity, Alert, RefreshControl } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import * as LocalAuthentication from "expo-local-authentication";
import { useUIStore, usePositionsStore, useOrdersStore } from "../store/index";
import { Colors, MONO, radius, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { formatPriceCents, formatShares, formatUsd, formatDateTime } from "../utils/format";
import { Order, Position } from "../types/api";
import { Card, EmptyState, ScreenHeader, Segmented, SideChip, StateChip, Stat } from "../components/ui";
import { RulesPanel } from "./RulesScreen";

type Tab = "positions" | "orders" | "rules";

const TERMINAL = ["FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"];

function orderTone(state: string): "long" | "short" | "alert" | "neutral" | "muted" {
  const s = state.toUpperCase();
  if (s === "FILLED") return "long";
  if (s === "REJECTED" || s === "FAILED" || s === "UNKNOWN") return "short";
  if (s === "CANCELLED" || s === "CANCELED" || s === "EXPIRED") return "muted";
  if (s.includes("DELAY")) return "alert";
  return "neutral";
}

export const PositionScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const { mode, paused } = useUIStore();
  const { positions, setPositions } = usePositionsStore();
  const { orders, setOrders } = useOrdersStore();
  const [tab, setTab] = useState<Tab>("positions");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, o] = await Promise.all([api.getPositions(), api.getOrders()]);
      if (p.ok && p.data) setPositions(p.data);
      if (o.ok && o.data) setOrders(o.data);
    } catch {
      // offline: keep last data
    }
  }, [setPositions, setOrders]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const closePosition = (pos: Position) => {
    Alert.alert(
      "Close position",
      `Create an exit proposal for ${pos.side.toUpperCase()} ${formatShares(pos.quantity)} shares?\n\nYou will still approve it from Home.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Create proposal",
          onPress: async () => {
            const auth = await LocalAuthentication.authenticateAsync({ promptMessage: "Confirm close position" });
            if (!auth.success) return;
            try {
              const r = await api.closePosition(pos.market_id, "biometric-confirmed");
              if (r.ok) Alert.alert("Exit proposal created", "Review and approve it from Home.");
              else Alert.alert("Error", r.error?.message ?? "Could not create exit proposal");
            } catch {
              Alert.alert("Error", "Network error");
            }
          },
        },
      ]
    );
  };

  const cancelOrder = (o: Order) => {
    Alert.alert("Cancel order", "Cancel this open order?", [
      { text: "Keep", style: "cancel" },
      {
        text: "Cancel order",
        style: "destructive",
        onPress: async () => {
          const auth = await LocalAuthentication.authenticateAsync({ promptMessage: "Confirm cancel order" });
          if (!auth.success) return;
          try {
            const r = await api.cancelOrder(o.id, "biometric-confirmed");
            if (r.ok) await load();
            else Alert.alert("Error", r.error?.message ?? "Cancel failed");
          } catch {
            Alert.alert("Error", "Network error");
          }
        },
      },
    ]);
  };

  const totalCost = positions.reduce((sum, p) => sum + (parseFloat(p.cost_usd) || 0), 0);
  const openOrders = orders.filter((o) => !TERMINAL.includes(o.state.toUpperCase())).length;

  return (
    <View style={styles.container}>
      <ScreenHeader title="Position" subtitle="Holdings, orders and exit rules" mode={mode} paused={paused} />
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.textSecondary} />}
      >
        <Card style={styles.summary}>
          <Stat label="Open positions" value={String(positions.length)} />
          <Stat label="Cost basis" value={formatUsd(totalCost)} />
          <Stat label="Open orders" value={String(openOrders)} />
        </Card>

        <View style={{ marginTop: 14 }}>
          <Segmented
            options={[
              { label: "Positions", value: "positions" },
              { label: "Orders", value: "orders" },
              { label: "Rules", value: "rules" },
            ]}
            value={tab}
            onChange={setTab}
          />
        </View>

        {tab === "positions" &&
          (positions.length === 0 ? (
            <EmptyState title="No open positions" hint="Approved trades will appear here." />
          ) : (
            positions.map((p) => (
              <View key={`${p.market_id}-${p.side}`} style={styles.card}>
                <View style={styles.cardHead}>
                  <SideChip side={p.side} />
                  <StateChip label={p.status.toUpperCase()} tone="muted" />
                </View>
                <Text style={styles.market} numberOfLines={1}>
                  {p.market_id}
                </Text>
                <View style={styles.grid}>
                  <Stat label="Shares" value={formatShares(p.quantity)} />
                  <Stat label="Avg price" value={formatPriceCents(p.avg_price_cents)} />
                  <Stat label="Cost" value={formatUsd(p.cost_usd)} />
                </View>
                <Text style={styles.meta}>Opened {formatDateTime(p.opened_at)}</Text>
                <TouchableOpacity style={styles.closeBtn} onPress={() => closePosition(p)}>
                  <Text style={styles.closeText}>Close position</Text>
                </TouchableOpacity>
              </View>
            ))
          ))}

        {tab === "orders" &&
          (orders.length === 0 ? (
            <EmptyState title="No orders yet" />
          ) : (
            orders.map((o) => {
              const open = !TERMINAL.includes(o.state.toUpperCase());
              return (
                <View key={o.id} style={styles.card}>
                  <View style={styles.cardHead}>
                    <SideChip side={o.side} />
                    <StateChip label={o.state.toUpperCase()} tone={orderTone(o.state)} />
                  </View>
                  <View style={styles.grid}>
                    <Stat label="Price" value={`${o.price_cents}¢`} />
                    <Stat label="Qty" value={formatShares(o.quantity)} />
                    <Stat label="Filled" value={formatShares(o.filled_qty)} />
                  </View>
                  <Text style={styles.meta}>
                    {formatDateTime(o.created_at)}
                    {o.reduce_only ? " · reduce-only" : ""}
                  </Text>
                  {open && (
                    <TouchableOpacity style={styles.closeBtn} onPress={() => cancelOrder(o)}>
                      <Text style={styles.closeText}>Cancel order</Text>
                    </TouchableOpacity>
                  )}
                </View>
              );
            })
          ))}

        {tab === "rules" && <RulesPanel />}
      </ScrollView>
    </View>
  );
};

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: c.background },
    content: { paddingHorizontal: 16, paddingBottom: 32 },
    summary: { flexDirection: "row", gap: 12 },
    card: {
      backgroundColor: c.surface,
      borderRadius: radius.lg,
      borderWidth: 1,
      borderColor: c.border,
      padding: 14,
      marginTop: 10,
      gap: 10,
    },
    cardHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
    market: { color: c.textSecondary, fontFamily: MONO, fontSize: 12 },
    grid: { flexDirection: "row", gap: 12 },
    meta: { color: c.textMuted, fontSize: 12 },
    closeBtn: {
      borderWidth: 1,
      borderColor: c.border,
      borderRadius: radius.md,
      minHeight: 44,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: c.surfaceAlt,
    },
    closeText: { color: c.text, fontSize: 14, fontWeight: "700" },
  });

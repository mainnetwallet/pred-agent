// Home screen — status strip (PAPER/LIVE + paused), balance, exposure, daily PnL,
// pending proposals count, live fixtures list (kit/08_MOBILE_APP.md)

import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useUIStore, useProposalsStore } from "../store/index";
import { Colors, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { StatusBanner, MoneyDisplay, OfflineBadge } from "../components/common";

export const HomeScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const { setStatus, mode, paused, feedsOk, balance, exposure, dailyPnl } = useUIStore();
  const { proposals, setProposals } = useProposalsStore();
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setRefreshing(true);
    try {
      const statusResp = await api.getStatus();
      if (statusResp.ok && statusResp.data) {
        setStatus(statusResp.data);
      }
      const proposalsResp = await api.getProposals("pending");
      if (proposalsResp.ok && proposalsResp.data) {
        setProposals(proposalsResp.data);
      }
    } catch {
      // error handled by stale badge
    } finally {
      setRefreshing(false);
    }
  };

  const pendingCount = proposals.filter((p) => p.status === "PROPOSED").length;

  return (
    <View style={styles.container}>
      <StatusBanner mode={mode} paused={paused} />
      <View style={styles.content}>
        <View style={styles.statsRow}>
          <MoneyDisplay amount={balance} label="Balance" />
          <MoneyDisplay amount={exposure} label="Exposure" />
          <MoneyDisplay amount={dailyPnl} label="Daily PnL" />
        </View>
        <OfflineBadge stale={!feedsOk} lastUpdate={null} />
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Pending Proposals ({pendingCount})</Text>
          {proposals.length === 0 ? (
            <Text style={styles.empty}>No pending proposals</Text>
          ) : (
            <FlatList
              data={proposals.filter((p) => p.status === "PROPOSED")}
              keyExtractor={(item) => item.id}
              renderItem={({ item }) => (
                <View style={styles.proposalRow}>
                  <Text style={styles.proposalSide}>{item.side.toUpperCase()}</Text>
                  <Text style={styles.proposalPrice}>
                    @ {item.price_cents}c
                  </Text>
                  <Text style={styles.proposalAmount}>
                    ${item.amount_usd}
                  </Text>
                </View>
              )}
            />
          )}
        </View>
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Live Fixtures</Text>
          <ActivityIndicator size="small" color={c.textSecondary} animating={refreshing} />
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
  statsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 16,
  },
  section: {
    marginBottom: 16,
  },
  sectionTitle: {
    color: c.text,
    fontSize: 16,
    fontWeight: "600",
    marginBottom: 8,
  },
  fixtureRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
  },
  fixtureName: {
    color: c.text,
    fontSize: 14,
    fontWeight: "500",
  },
  fixtureStatus: {
    fontSize: 12,
    color: c.textSecondary,
  },
  proposalRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
  },
  proposalSide: {
    color: c.text,
    fontSize: 13,
    fontWeight: "600",
  },
  proposalPrice: {
    fontSize: 13,
    color: c.textSecondary,
  },
  proposalAmount: {
    color: c.text,
    fontSize: 13,
    fontWeight: "600",
  },
  empty: {
    fontSize: 14,
    color: c.textSecondary,
    textAlign: "center",
    paddingVertical: 20,
  },
});

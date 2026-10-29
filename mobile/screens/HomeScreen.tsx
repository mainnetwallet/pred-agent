// Home — dashboard: balance hero, exposure / daily PnL, pending proposals, live fixtures.

import React, { useCallback, useState } from "react";
import { View, Text, ScrollView, StyleSheet, TouchableOpacity, RefreshControl } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { useUIStore, useProposalsStore } from "../store/index";
import { Colors, MONO, radius, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { formatUsd } from "../utils/format";
import { FixtureSummary } from "../types/api";
import { Card, EmptyState, Icon, ScreenHeader, SectionTitle, SideChip, Stat, StateChip, signedUsd } from "../components/ui";

const REFRESH_MS = 15000;

export const HomeScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const navigation = useNavigation<any>();
  const { setStatus, mode, paused, feedsOk, balance, exposure, dailyPnl } = useUIStore();
  const { proposals, setProposals } = useProposalsStore();
  const [fixtures, setFixtures] = useState<FixtureSummary[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [s, p, f] = await Promise.all([api.getStatus(), api.getProposals("pending"), api.getFixtures("live")]);
      if (s.ok && s.data) setStatus(s.data);
      if (p.ok && p.data) setProposals(p.data);
      if (f.ok && f.data) setFixtures(f.data);
    } catch {
      // offline: the stale badge covers it
    }
  }, [setStatus, setProposals]);

  // Refresh when the tab gains focus and keep polling while it is visible.
  useFocusEffect(
    useCallback(() => {
      loadData();
      const t = setInterval(loadData, REFRESH_MS);
      return () => clearInterval(t);
    }, [loadData])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    await loadData();
    setRefreshing(false);
  };

  const pending = proposals.filter((p) => p.status === "PROPOSED");
  const pnl = signedUsd(dailyPnl);

  return (
    <View style={styles.container}>
      <ScreenHeader title="PRED Agent" logo subtitle={feedsOk ? "Feeds connected" : undefined} mode={mode} paused={paused} />
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.textSecondary} />}
      >
        {!feedsOk && mode ? (
          <View style={styles.stale}>
            <Text style={styles.staleText}>STALE — data feeds offline, actions may be unavailable</Text>
          </View>
        ) : null}

        <Card style={styles.hero}>
          <Text style={styles.heroLabel}>Balance</Text>
          <Text style={styles.heroValue}>{balance == null ? "—" : formatUsd(balance)}</Text>
          {balance == null && feedsOk ? (
            <Text style={styles.heroHint}>Balance unavailable — the server could not fetch it from PRED.</Text>
          ) : null}
          <View style={styles.divider} />
          <View style={styles.statsRow}>
            <Stat label="Exposure" value={formatUsd(exposure)} />
            <Stat label="Daily PnL" value={pnl.text} tone={pnl.tone} />
          </View>
        </Card>

        <SectionTitle
          title={`Needs your decision${pending.length ? ` · ${pending.length}` : ""}`}
          action={pending.length ? "Review all" : undefined}
          onAction={() => navigation.navigate("Proposals")}
        />
        {pending.length === 0 ? (
          <Card>
            <EmptyState title="Nothing waiting" hint="New trade proposals from the agent will show up here." />
          </Card>
        ) : (
          pending.slice(0, 3).map((p) => (
            <TouchableOpacity
              key={p.id}
              activeOpacity={0.8}
              onPress={() => navigation.navigate("Proposals")}
              style={styles.rowCard}
            >
              <View style={styles.rowTop}>
                <SideChip side={p.side} />
                {p.advisory ? <StateChip label="ADVISORY" tone="alert" /> : null}
                <View style={{ flex: 1 }} />
                <Icon name="chevron" size={18} color={c.textMuted} />
              </View>
              <View style={styles.rowBottom}>
                <Text style={styles.mono}>@ {p.price_cents}¢</Text>
                <Text style={styles.mono}>{formatUsd(p.amount_usd)}</Text>
                <Text style={[styles.mono, { color: c.long }]}>+{parseFloat(p.edge_pp).toFixed(1)} pp</Text>
              </View>
            </TouchableOpacity>
          ))
        )}

        <SectionTitle title="Live now" action={fixtures.length ? "See all" : undefined} onAction={() => navigation.navigate("Live")} />
        {fixtures.length === 0 ? (
          <Card>
            <EmptyState title="No live matches" hint="Upcoming fixtures are on the Live tab." />
          </Card>
        ) : (
          fixtures.slice(0, 4).map((f) => (
            <TouchableOpacity
              key={f.id}
              activeOpacity={0.8}
              style={styles.rowCard}
              onPress={() => navigation.navigate("FixtureDetail", { id: f.id })}
            >
              <View style={styles.fixtureRow}>
                <Text style={styles.teams} numberOfLines={1}>
                  {f.home_team ?? "?"} <Text style={styles.vs}>vs</Text> {f.away_team ?? "?"}
                </Text>
                <Text style={styles.score}>
                  {f.home_score != null ? `${f.home_score}–${f.away_score ?? 0}` : "—"}
                </Text>
              </View>
              <View style={styles.rowTop}>
                <StateChip label={f.minute != null ? `${f.minute}′` : f.game_status ?? "LIVE"} tone="short" />
                {f.league ? <Text style={styles.league}>{f.league}</Text> : null}
              </View>
            </TouchableOpacity>
          ))
        )}
      </ScrollView>
    </View>
  );
};

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: c.background },
    content: { paddingHorizontal: 16, paddingBottom: 32 },
    stale: { backgroundColor: c.pausedBg, borderColor: c.pausedBorder, borderWidth: 1, borderRadius: radius.md, padding: 10, marginBottom: 12 },
    staleText: { color: c.pausedBorder, fontSize: 12, fontWeight: "700" },
    hero: { paddingVertical: 20 },
    heroLabel: { color: c.textSecondary, fontSize: 12, fontWeight: "700", letterSpacing: 0.8, textTransform: "uppercase" },
    heroValue: { color: c.text, fontFamily: MONO, fontSize: 38, fontWeight: "800", marginTop: 4, letterSpacing: -1 },
    heroHint: { color: c.alert, fontSize: 12, marginTop: 6 },
    divider: { height: 1, backgroundColor: c.border, marginVertical: 16 },
    statsRow: { flexDirection: "row", gap: 16 },
    rowCard: {
      backgroundColor: c.surface,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.border,
      padding: 14,
      marginBottom: 8,
      gap: 10,
    },
    rowTop: { flexDirection: "row", alignItems: "center", gap: 8 },
    rowBottom: { flexDirection: "row", justifyContent: "space-between" },
    mono: { color: c.text, fontFamily: MONO, fontSize: 14, fontWeight: "700" },
    fixtureRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
    teams: { color: c.text, fontSize: 15, fontWeight: "700", flex: 1 },
    vs: { color: c.textMuted, fontWeight: "500" },
    score: { color: c.text, fontFamily: MONO, fontSize: 18, fontWeight: "800" },
    league: { color: c.textSecondary, fontSize: 12 },
  });

// Fixture detail — score header, p_fair per outcome, market quotes, research.

import React, { useCallback, useEffect, useState } from "react";
import { View, Text, ScrollView, StyleSheet, Linking, TouchableOpacity, ActivityIndicator, TextInput, Alert } from "react-native";
import { useNavigation, useRoute } from "@react-navigation/native";
import { useUIStore } from "../store/index";
import { Colors, MONO, radius, useTheme, useThemedStyles } from "../utils/theme";
import { api } from "../services/api";
import { formatDateTime, formatUsd } from "../utils/format";
import { FixtureDetail } from "../types/api";
import { ProbabilityGauge } from "../components/ProbabilityGauge";
import { Card, EmptyState, ScreenHeader, SectionTitle, StateChip } from "../components/ui";

// One market: amount + Buy YES / Buy NO. Creates a PENDING proposal; you approve it on the Proposals screen.
const TradeRow: React.FC<{ marketId: string; bid?: number | null; ask?: number | null; first: boolean }> = ({
  marketId,
  bid,
  ask,
  first,
}) => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const navigation = useNavigation<any>();
  const [amount, setAmount] = useState("1");
  const [busy, setBusy] = useState(false);

  const place = async (side: "long" | "short") => {
    const a = parseFloat(amount);
    if (!(a > 0)) {
      Alert.alert("Amount needed", "Enter an amount in USD.");
      return;
    }
    setBusy(true);
    try {
      const r = await api.createProposal({ market_id: marketId, side, amount_usd: String(a) });
      if (r.ok) {
        Alert.alert("Proposal created", "Nothing is placed until you approve it.", [
          { text: "Later", style: "cancel" },
          { text: "Review now", onPress: () => navigation.navigate("Proposals") },
        ]);
      } else {
        Alert.alert("Could not create proposal", r.error?.message ?? "Failed");
      }
    } catch {
      Alert.alert("Error", "Network error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={[styles.tradeRow, !first && styles.quoteRowBorder]}>
      <Text style={styles.quoteId} numberOfLines={1}>
        {marketId}
      </Text>
      <View style={styles.tradeInputRow}>
        <Text style={styles.muted}>Amount $</Text>
        <TextInput
          style={styles.tradeInput}
          value={amount}
          onChangeText={setAmount}
          keyboardType="decimal-pad"
          placeholderTextColor={c.placeholder}
          keyboardAppearance={c.isDark ? "dark" : "light"}
          accessibilityLabel="Amount in USD"
        />
      </View>
      <View style={styles.tradeBtns}>
        <TouchableOpacity
          style={[styles.tradeBtn, { backgroundColor: c.long }, busy && { opacity: 0.5 }]}
          disabled={busy}
          onPress={() => place("long")}
          accessibilityRole="button"
        >
          <Text style={styles.tradeBtnText}>Buy YES{ask ? ` · ${ask}¢` : ""}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tradeBtn, { backgroundColor: c.short }, busy && { opacity: 0.5 }]}
          disabled={busy}
          onPress={() => place("short")}
          accessibilityRole="button"
        >
          <Text style={styles.tradeBtnText}>Buy NO{bid ? ` · ${bid}¢` : ""}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
};

export const FixtureDetailScreen: React.FC = () => {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const { mode, paused } = useUIStore();
  const id: string = route.params?.id;
  const [fx, setFx] = useState<FixtureDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.getFixtureDetail(id);
      if (r.ok && r.data) {
        setFx(r.data);
        setFailed(false);
      } else {
        setFailed(true);
      }
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  const outcomes: { label: string; p: number | null | undefined; side: "long" | "short" | "neutral" }[] = fx
    ? [
        { label: fx.home_team ?? "Home", p: fx.p_fair_home, side: "long" },
        { label: "Draw", p: fx.p_fair_draw, side: "neutral" },
        { label: fx.away_team ?? "Away", p: fx.p_fair_away, side: "short" },
      ]
    : [];

  return (
    <View style={styles.container}>
      <ScreenHeader
        title="Match"
        subtitle={fx?.league ?? undefined}
        mode={mode}
        paused={paused}
        onBack={() => navigation.goBack()}
      />
      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={c.textSecondary} />
      ) : failed || !fx ? (
        <EmptyState title="Could not load this match" hint="Check your connection and try again." />
      ) : (
        <ScrollView contentContainerStyle={styles.content}>
          <Card>
            <View style={styles.statusRow}>
              {fx.state === "live" ? (
                <StateChip label={fx.minute != null ? `LIVE · ${fx.minute}′` : "LIVE"} tone="short" />
              ) : (
                <StateChip label={formatDateTime(fx.kickoff)} tone="neutral" />
              )}
            </View>
            <View style={styles.scoreRow}>
              <Text style={styles.team} numberOfLines={2}>
                {fx.home_team ?? "?"}
              </Text>
              <Text style={styles.score}>
                {fx.home_score ?? "–"} : {fx.away_score ?? "–"}
              </Text>
              <Text style={[styles.team, { textAlign: "right" }]} numberOfLines={2}>
                {fx.away_team ?? "?"}
              </Text>
            </View>
          </Card>

          <SectionTitle title="Agent's fair probability" />
          <Card>
            {outcomes.every((o) => o.p == null) ? (
              <Text style={styles.muted}>No estimate yet for this match.</Text>
            ) : (
              outcomes.map((o) => (
                <View key={o.label} style={styles.outcome}>
                  <View style={styles.outcomeHead}>
                    <Text style={styles.outcomeName}>{o.label}</Text>
                    <Text style={styles.outcomePct}>{o.p != null ? `${(o.p * 100).toFixed(0)}%` : "—"}</Text>
                  </View>
                  <ProbabilityGauge marketPrice={null} pFair={o.p ?? null} pLow={null} pHigh={null} side={o.side} />
                </View>
              ))
            )}
            {fx.max_suggested_usd ? (
              <Text style={styles.muted}>Max suggested stake: {formatUsd(fx.max_suggested_usd)}</Text>
            ) : null}
          </Card>

          {fx.markets.length > 0 && (
            <>
              <SectionTitle title="Market quotes" />
              <Card style={{ paddingVertical: 4 }}>
                {fx.markets.map((m, i) => (
                  <View key={m.market_id} style={[styles.quoteRow, i > 0 && styles.quoteRowBorder]}>
                    <Text style={styles.quoteId} numberOfLines={1}>
                      {m.market_id}
                    </Text>
                    <Text style={styles.quoteVal}>
                      {m.best_bid_cents ?? "—"} / {m.best_ask_cents ?? "—"}¢
                    </Text>
                  </View>
                ))}
              </Card>
            </>
          )}

          {fx.markets.length > 0 && (
            <>
              <SectionTitle title="Trade" />
              <Card style={{ paddingVertical: 4 }}>
                {fx.markets.map((m, i) => (
                  <TradeRow key={m.market_id} marketId={m.market_id} bid={m.best_bid_cents} ask={m.best_ask_cents} first={i === 0} />
                ))}
                <Text style={styles.muted}>Creates a pending proposal — nothing is placed until you approve it.</Text>
              </Card>
            </>
          )}

          {fx.research_summary ? (
            <>
              <SectionTitle title="Research" />
              <Card>
                <Text style={styles.body}>{fx.research_summary}</Text>
                {fx.research_sources?.map((s) => (
                  <TouchableOpacity key={s} onPress={() => Linking.openURL(s)}>
                    <Text style={styles.link} numberOfLines={1}>
                      {s}
                    </Text>
                  </TouchableOpacity>
                ))}
              </Card>
            </>
          ) : null}
        </ScrollView>
      )}
    </View>
  );
};

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: c.background },
    content: { paddingHorizontal: 16, paddingBottom: 32 },
    statusRow: { flexDirection: "row", marginBottom: 12 },
    scoreRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
    team: { color: c.text, fontSize: 16, fontWeight: "700", flex: 1 },
    score: { color: c.text, fontFamily: MONO, fontSize: 28, fontWeight: "800" },
    outcome: { marginBottom: 6 },
    outcomeHead: { flexDirection: "row", justifyContent: "space-between" },
    outcomeName: { color: c.text, fontSize: 14, fontWeight: "600" },
    outcomePct: { color: c.text, fontFamily: MONO, fontSize: 14, fontWeight: "800" },
    muted: { color: c.textSecondary, fontSize: 13, marginTop: 6 },
    quoteRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 12, gap: 12 },
    quoteRowBorder: { borderTopWidth: 1, borderTopColor: c.border },
    quoteId: { color: c.textSecondary, fontSize: 12, flex: 1, fontFamily: MONO },
    quoteVal: { color: c.text, fontFamily: MONO, fontSize: 14, fontWeight: "700" },
    tradeRow: { paddingVertical: 12, gap: 10 },
    tradeInputRow: { flexDirection: "row", alignItems: "center", gap: 8 },
    tradeInput: {
      flex: 1,
      maxWidth: 140,
      backgroundColor: c.inputBackground,
      borderWidth: 1,
      borderColor: c.border,
      borderRadius: radius.md,
      paddingHorizontal: 12,
      paddingVertical: 8,
      color: c.text,
      fontFamily: MONO,
      fontSize: 15,
    },
    tradeBtns: { flexDirection: "row", gap: 10 },
    tradeBtn: { flex: 1, minHeight: 44, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
    tradeBtnText: { color: "#fff", fontSize: 14, fontWeight: "700" },
    body: { color: c.text, fontSize: 14, lineHeight: 21 },
    link: { color: c.primary, fontSize: 12, marginTop: 8, borderRadius: radius.sm },
  });

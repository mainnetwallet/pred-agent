/**
 * ChatScreen — multi-turn chat with the PRED Agent, laid out like the ChatGPT app:
 * empty-state greeting with suggestion pills, full-width assistant replies
 * (markdown) on the page background, right-aligned user bubbles, and a rounded
 * composer with a round send button.
 *
 * Keeps: streaming, auto-scroll, loading + error + llm_not_configured states,
 * new chat, and card rendering (rule/proposal confirm with the X-Confirm gate).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Animated,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { api } from "../services/api";
import { ChatCard } from "../types/api";
import { useUIStore } from "../store/index";
import { Colors, radius, useTheme, useThemedStyles } from "../utils/theme";
import { Icon, ScreenHeader } from "../components/ui";
import { Markdown } from "../components/Markdown";

// ── Types ─────────────────────────────────────────────────────
interface LocalMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  cards?: ChatCard[];
}

const QUICK_CHIPS = [
  { label: "🟢 Live matches", message: "ekhon live match ki ki ache?" },
  { label: "🕐 Upcoming", message: "upcoming match gulo dekhao" },
  { label: "📊 Positions", message: "আমার positions দেখাও" },
  { label: "📋 Rules", message: "sob rules dekhao" },
  { label: "⚙️ Status", message: "/status" },
];

// ── Card renderer ─────────────────────────────────────────────
function CardView({
  card,
  onConfirmRule,
  onCancelRule,
  onApproveProposal,
  onRejectProposal,
  onAction,
}: {
  card: ChatCard;
  onConfirmRule: (id: string) => void;
  onCancelRule: (id: string) => void;
  onApproveProposal: (id: string) => void;
  onRejectProposal: (id: string) => void;
  onAction: (data: Record<string, any>) => void;
}) {
  const styles = useThemedStyles(makeStyles);
  if (card.type === "fixture_list") {
    const fixtures: any[] = (card.data as any).fixtures ?? [];
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>FIXTURES</Text>
        {fixtures.slice(0, 6).map((fx: any, i: number) => (
          <View key={i} style={styles.fixtureRow}>
            <Text style={[styles.badge, fx.state === "live" ? styles.badgeLive : styles.badgeUpcoming]}>
              {(fx.state ?? "?").toUpperCase()}
            </Text>
            <Text style={styles.fixtureText}>
              {fx.home_team ?? "?"} vs {fx.away_team ?? "?"}
              {fx.home_score != null ? `  ${fx.home_score}–${fx.away_score}` : ""}
            </Text>
          </View>
        ))}
      </View>
    );
  }

  if (card.type === "rule") {
    const d = card.data as any;
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>EXIT RULE</Text>
        <Text style={styles.cardBody}>
          {d.kind} — {d.trigger_type} @ {d.trigger_value} (size: {d.size ?? "all"})
        </Text>
        <Text style={styles.cardMeta}>Status: {d.status}</Text>
        {d.status === "PENDING" && (
          <View style={styles.cardActions}>
            <TouchableOpacity style={[styles.btn, styles.btnOk]} onPress={() => onConfirmRule(d.id)}>
              <Text style={styles.btnText}>✓ Confirm</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.btn, styles.btnBad]} onPress={() => onCancelRule(d.id)}>
              <Text style={styles.btnText}>✗ Cancel</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    );
  }

  if (card.type === "proposal") {
    const d = card.data as any;
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>TRADE PROPOSAL</Text>
        <Text style={styles.cardBody}>
          {d.side} @ {d.price_cents}¢ · ${d.amount_usd} · {d.edge_pp}pp edge
        </Text>
        {d.status === "PROPOSED" && (
          <View style={styles.cardActions}>
            <TouchableOpacity style={[styles.btn, styles.btnOk]} onPress={() => onApproveProposal(d.id)}>
              <Text style={styles.btnText}>✓ Approve</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.btn, styles.btnBad]} onPress={() => onRejectProposal(d.id)}>
              <Text style={styles.btnText}>✗ Reject</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    );
  }

  if (card.type === "action") {
    const d = card.data as any;
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>ACTION REQUIRED</Text>
        <Text style={styles.cardBody}>{d.label ?? "Confirm this action?"}</Text>
        <View style={styles.cardActions}>
          <TouchableOpacity style={[styles.btn, styles.btnBad]} onPress={() => onAction(d)}>
            <Text style={styles.btnText}>Confirm</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return null;
}

// ── Thinking indicator (pulsing dot, like ChatGPT) ────────────
function Thinking({ label }: { label: string }) {
  const styles = useThemedStyles(makeStyles);
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 650, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 650, useNativeDriver: true }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);
  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.7, 1.15] });
  const opacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.5, 1] });
  return (
    <View style={styles.thinkingRow}>
      <Animated.View style={[styles.thinkingDot, { opacity, transform: [{ scale }] }]} />
      <Text style={styles.loadingText}>{label}</Text>
    </View>
  );
}

// ── Empty state: greeting + suggestion pills ──────────────────
function EmptyState({ onPick }: { onPick: (message: string) => void }) {
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  return (
    <View style={styles.empty}>
      <View style={styles.emptyLogo}>
        <Icon name="chat" size={28} color={c.primary} />
      </View>
      <Text style={styles.emptyTitle}>What can I help with?</Text>
      <Text style={styles.emptySub}>Live match, positions, rules — বাংলা, Banglish বা English-এ জিজ্ঞেস করুন।</Text>
      <View style={styles.suggestList}>
        {QUICK_CHIPS.map((chip) => (
          <TouchableOpacity
            key={chip.label}
            style={styles.suggest}
            onPress={() => onPick(chip.message)}
            accessibilityRole="button"
          >
            <Text style={styles.suggestText}>{chip.label}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  );
}

// ── ChatScreen ────────────────────────────────────────────────
export default function ChatScreen() {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toolName, setToolName] = useState<string | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const c = useTheme();
  const styles = useThemedStyles(makeStyles);
  const { mode, paused } = useUIStore();

  const isEmpty = messages.length === 0;
  const canSend = input.trim().length > 0 && !loading;

  const scrollToBottom = useCallback(() => {
    setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 100);
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages, scrollToBottom]);

  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    const userMsg: LocalMessage = { id: Date.now().toString(), role: "user", content: trimmed };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setError(null);

    const aiId = (Date.now() + 1).toString();
    let started = false;
    const upsert = (patch: (m: LocalMessage) => LocalMessage) => {
      setMessages((prev) => {
        if (!prev.some((m) => m.id === aiId)) {
          return [...prev, patch({ id: aiId, role: "assistant", content: "" })];
        }
        return prev.map((m) => (m.id === aiId ? patch(m) : m));
      });
    };

    try {
      const r = await api.chatStream(trimmed, sessionId, {
        onSession: (sid) => { if (!sessionId) setSessionId(sid); },
        onDelta: (t) => {
          started = true;
          setToolName(null);
          upsert((m) => ({ ...m, content: m.content + t }));
        },
        onTool: (name) => setToolName(name),
        onReset: () => upsert((m) => ({ ...m, content: "" })),
        onCards: (cards) => {
          started = true;
          upsert((m) => ({ ...m, cards: [...(m.cards ?? []), ...cards] }));
        },
      });
      if (r.ok && r.data) {
        if (!sessionId) setSessionId(r.data.session_id);
        // Replace streamed text with the final canonical reply + cards.
        upsert((m) => ({ ...m, content: r.data!.reply, cards: r.data!.cards }));
      } else {
        if (started) setMessages((prev) => prev.filter((m) => m.id !== aiId));
        if (r.error?.code === "llm_not_configured") {
          setError("LLM কনফিগার করা নেই। সার্ভারে LLM_API_KEY এবং LLM_MODEL সেট করুন।");
        } else if (r.error?.code === "rate_limited") {
          setError("বেশি বার্তা পাঠাচ্ছেন। একটু অপেক্ষা করুন।");
        } else {
          setError(r.error?.message ?? "Chat error. Try again.");
        }
      }
    } catch (_e) {
      setError("Network error. Check connection.");
    } finally {
      setToolName(null);
      setLoading(false);
    }
  }, [loading, sessionId]);

  const newChat = useCallback(() => {
    if (messages.length === 0 && !sessionId) return;
    Alert.alert("New chat", "Start a new chat? The current conversation will be cleared.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "New chat",
        style: "destructive",
        onPress: async () => {
          if (sessionId) await api.clearChat(sessionId);
          setSessionId(null);
          setMessages([]);
          setError(null);
        },
      },
    ]);
  }, [messages.length, sessionId]);

  const handleConfirmRule = useCallback(async (ruleId: string) => {
    const r = await api.confirmRule(ruleId, "biometric-confirmed");
    Alert.alert(r.ok ? "✓ Rule Confirmed" : "Error", r.ok ? "Exit rule is now ACTIVE." : (r.error?.message ?? "Failed"));
  }, []);

  const handleCancelRule = useCallback(async (ruleId: string) => {
    const r = await api.cancelRule(ruleId);
    Alert.alert(r.ok ? "Cancelled" : "Error", r.ok ? "Rule cancelled." : (r.error?.message ?? "Failed"));
  }, []);

  const handleApproveProposal = useCallback(async (proposalId: string) => {
    const r = await api.approveProposal(proposalId, {}, "biometric-confirmed");
    Alert.alert(r.ok ? "✓ Approved" : "Error", r.ok ? "Proposal approved." : (r.error?.message ?? "Failed"));
  }, []);

  const handleRejectProposal = useCallback(async (proposalId: string) => {
    const r = await api.rejectProposal(proposalId);
    Alert.alert(r.ok ? "Rejected" : "Error", r.ok ? "Proposal rejected." : (r.error?.message ?? "Failed"));
  }, []);

  const handleAction = useCallback(async (d: Record<string, any>) => {
    const action = d.action as string;
    if (action === "pause") {
      const r = await api.pause("biometric-confirmed");
      Alert.alert(r.ok ? "Paused" : "Error", r.ok ? "Agent paused." : (r.error?.message ?? "Failed"));
    } else if (action === "panic") {
      Alert.alert("PANIC", "Pause, reject proposals, cancel orders AND all rules?", [
        { text: "Cancel", style: "cancel" },
        {
          text: "PANIC",
          style: "destructive",
          onPress: async () => {
            const r = await api.panic("biometric-confirmed");
            Alert.alert(r.ok ? "Panic done" : "Error", r.ok ? "Agent paused, everything cancelled." : (r.error?.message ?? "Failed"));
          },
        },
      ]);
    } else if (action === "cancel_order") {
      const r = await api.cancelOrder(d.order_id, "biometric-confirmed");
      Alert.alert(r.ok ? "Cancelled" : "Error", r.ok ? "Order cancelled." : (r.error?.message ?? "Failed"));
    } else if (action === "cancel_rule") {
      const r = await api.cancelRule(d.rule_id);
      Alert.alert(r.ok ? "Cancelled" : "Error", r.ok ? "Rule cancelled." : (r.error?.message ?? "Failed"));
    }
  }, []);

  const last = messages[messages.length - 1];
  const showThinking = loading && (!!toolName || last?.role === "user");

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={0}
    >
      <ScreenHeader
        title="Agent chat"
        mode={mode}
        paused={paused}
        right={
          <TouchableOpacity style={styles.headerBtn} onPress={newChat} accessibilityLabel="New chat" hitSlop={8}>
            <Icon name="compose" size={19} color={c.textSecondary} />
          </TouchableOpacity>
        }
      />

      {/* Conversation */}
      <ScrollView
        ref={scrollRef}
        style={styles.messages}
        contentContainerStyle={[styles.messagesContent, isEmpty && styles.messagesEmpty]}
        keyboardDismissMode="on-drag"
        keyboardShouldPersistTaps="handled"
      >
        {isEmpty && !loading ? (
          <EmptyState onPick={sendMessage} />
        ) : (
          messages.map((msg) =>
            msg.role === "user" ? (
              <View key={msg.id} style={styles.userRow}>
                <View style={styles.userBubble}>
                  {/* Text never executes markup */}
                  <Text style={styles.userText} selectable>{msg.content}</Text>
                </View>
              </View>
            ) : (
              <View key={msg.id} style={styles.aiRow}>
                {!!msg.content && <Markdown text={msg.content} />}
                {msg.cards?.map((card, i) => (
                  <CardView
                    key={i}
                    card={card}
                    onConfirmRule={handleConfirmRule}
                    onCancelRule={handleCancelRule}
                    onApproveProposal={handleApproveProposal}
                    onRejectProposal={handleRejectProposal}
                    onAction={handleAction}
                  />
                ))}
              </View>
            )
          )
        )}
        {showThinking && (
          <View style={styles.aiRow}>
            <Thinking label={toolName ? `Using ${toolName}…` : "Thinking…"} />
          </View>
        )}
        {error && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorText}>⚠️ {error}</Text>
          </View>
        )}
      </ScrollView>

      {/* Composer */}
      <View style={styles.composerWrap}>
        <View style={styles.composer}>
          <TextInput
            style={styles.textInput}
            value={input}
            onChangeText={setInput}
            placeholder="Message PRED Agent"
            placeholderTextColor={c.placeholder}
            keyboardAppearance={c.isDark ? "dark" : "light"}
            underlineColorAndroid="transparent"
            multiline
            maxLength={4000}
          />
          <TouchableOpacity
            style={[styles.sendBtn, !canSend && styles.sendBtnDisabled]}
            onPress={() => sendMessage(input)}
            disabled={!canSend}
            accessibilityLabel="Send message"
          >
            <Icon name="send" size={20} color={canSend ? c.background : c.textMuted} strokeWidth={2.2} />
          </TouchableOpacity>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

// ── Styles ────────────────────────────────────────────────────
const makeStyles = (c: Colors) => StyleSheet.create({
  container: { flex: 1, backgroundColor: c.background },
  headerBtn: { width: 40, height: 40, borderRadius: radius.pill, alignItems: "center", justifyContent: "center", backgroundColor: c.surface, borderWidth: 1, borderColor: c.border },
  messages: { flex: 1 },
  messagesContent: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 12, gap: 20 },
  messagesEmpty: { flexGrow: 1, justifyContent: "center" },

  // user: right-aligned soft bubble; assistant: plain full-width text
  userRow: { alignItems: "flex-end" },
  userBubble: { maxWidth: "82%", backgroundColor: c.surfaceAlt, borderRadius: 20, paddingHorizontal: 16, paddingVertical: 10 },
  userText: { color: c.text, fontSize: 16, lineHeight: 23 },
  aiRow: { alignSelf: "stretch", gap: 10 },
  thinkingRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 4 },
  thinkingDot: { width: 12, height: 12, borderRadius: 6, backgroundColor: c.text },
  loadingText: { color: c.textSecondary, fontSize: 14 },
  errorBanner: { backgroundColor: c.shortSoft, borderRadius: radius.md, padding: 10 },
  errorText: { color: c.short, fontSize: 13 },

  // empty state
  empty: { alignItems: "center", paddingHorizontal: 8, gap: 8 },
  emptyLogo: { width: 60, height: 60, borderRadius: 30, backgroundColor: c.primarySoft, alignItems: "center", justifyContent: "center", marginBottom: 6 },
  emptyTitle: { color: c.text, fontSize: 26, fontWeight: "700", letterSpacing: -0.4, textAlign: "center" },
  emptySub: { color: c.textSecondary, fontSize: 14, lineHeight: 20, textAlign: "center", maxWidth: 320 },
  suggestList: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", gap: 8, marginTop: 16 },
  suggest: { backgroundColor: c.surface, borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 10, borderWidth: 1, borderColor: c.border },
  suggestText: { color: c.text, fontSize: 14, fontWeight: "600" },

  // composer
  composerWrap: { paddingHorizontal: 12, paddingTop: 6, paddingBottom: 10 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 6, backgroundColor: c.surface, borderRadius: 26, borderWidth: 1, borderColor: c.border, paddingLeft: 18, paddingRight: 6, paddingVertical: 6 },
  textInput: { flex: 1, color: c.text, fontSize: 16, maxHeight: 140, minHeight: 36, paddingTop: Platform.OS === "ios" ? 8 : 6, paddingBottom: Platform.OS === "ios" ? 8 : 6 },
  sendBtn: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: c.text },
  sendBtnDisabled: { backgroundColor: c.surfaceAlt },

  // Cards (rule / proposal / action / fixtures)
  card: { borderRadius: 14, padding: 12, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border },
  cardTitle: { fontSize: 10, color: c.textSecondary, fontWeight: "800", letterSpacing: 0.8, textTransform: "uppercase", marginBottom: 6 },
  cardBody: { color: c.text, fontSize: 14, marginBottom: 4 },
  cardMeta: { color: c.textSecondary, fontSize: 12 },
  cardActions: { flexDirection: "row", gap: 8, marginTop: 8 },
  btn: { borderRadius: radius.pill, paddingHorizontal: 16, minHeight: 40, justifyContent: "center" },
  btnOk: { backgroundColor: c.long },
  btnBad: { backgroundColor: c.short },
  btnText: { color: c.onAccent, fontSize: 13, fontWeight: "700" },

  fixtureRow: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 3 },
  badge: { fontSize: 10, fontWeight: "700", paddingHorizontal: 5, paddingVertical: 2, borderRadius: 4, overflow: "hidden" },
  badgeLive: { backgroundColor: c.shortSoft, color: c.short },
  badgeUpcoming: { backgroundColor: c.primarySoft, color: c.primary },
  fixtureText: { color: c.text, fontSize: 13, flex: 1 },
});

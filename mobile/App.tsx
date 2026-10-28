// Main app entry — Expo Router not used; simple navigator with screens

import "react-native-gesture-handler";
import { useEffect, useState } from "react";
import {
  View,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { NavigationContainer, DarkTheme, DefaultTheme } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { createStackNavigator } from "@react-navigation/stack";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";
import { LoginScreen } from "./screens/LoginScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { LiveScreen } from "./screens/LiveScreen";
import ChatScreen from "./screens/ChatScreen";
import { PositionScreen } from "./screens/PositionScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { FixtureDetailScreen } from "./screens/FixtureDetailScreen";
import { ProposalsScreen } from "./screens/ProposalsScreen";
import { PanicScreen } from "./screens/PanicScreen";
import { Icon, IconName } from "./components/ui";
import { useAuthStore, useProposalsStore } from "./store/index";
import { wsClient } from "./services/websocket";
import { api } from "./services/api";
import { useTheme } from "./utils/theme";

const Stack = createStackNavigator();
const Tab = createBottomTabNavigator();

const TAB_ICONS: Record<string, IconName> = {
  Home: "home",
  Live: "live",
  Chat: "chat",
  Position: "position",
  Settings: "settings",
};

// Bottom navigation: Home · Live · Chat · Position · Settings
function MainTabs() {
  const c = useTheme();
  const insets = useSafeAreaInsets();
  const pending = useProposalsStore((s) => s.proposals.filter((p) => p.status === "PROPOSED").length);
  return (
    <Tab.Navigator
      initialRouteName="Home"
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarHideOnKeyboard: true,
        tabBarActiveTintColor: c.primary,
        tabBarInactiveTintColor: c.textMuted,
        tabBarLabelStyle: { fontSize: 11, fontWeight: "700", marginBottom: 2 },
        tabBarStyle: {
          backgroundColor: c.tabBar,
          borderTopColor: c.border,
          borderTopWidth: 1,
          height: 58 + insets.bottom,
          paddingTop: 6,
          paddingBottom: insets.bottom > 0 ? insets.bottom : 6,
        },
        tabBarBadgeStyle: { backgroundColor: c.danger, color: c.onAccent, fontSize: 10, fontWeight: "800" },
        tabBarIcon: ({ color, focused }) => (
          <Icon name={TAB_ICONS[route.name]} color={color} size={23} strokeWidth={focused ? 2.2 : 1.8} />
        ),
      })}
    >
      <Tab.Screen name="Home" component={HomeScreen} options={{ tabBarBadge: pending > 0 ? pending : undefined }} />
      <Tab.Screen name="Live" component={LiveScreen} />
      <Tab.Screen name="Chat" component={ChatScreen} />
      <Tab.Screen name="Position" component={PositionScreen} />
      <Tab.Screen name="Settings" component={SettingsScreen} />
    </Tab.Navigator>
  );
}

export default function App() {
  const c = useTheme();
  const navTheme = {
    ...(c.isDark ? DarkTheme : DefaultTheme),
    colors: {
      ...(c.isDark ? DarkTheme : DefaultTheme).colors,
      background: c.background,
      card: c.surface,
      text: c.text,
      border: c.border,
      primary: c.primary,
    },
  };
  const { deviceToken, serverUrl, isAuthenticated } = useAuthStore();
  const [loading, setLoading] = useState(!useAuthStore.persist.hasHydrated());

  useEffect(() => {
    // Wait for the saved session to load from SecureStore before choosing a screen.
    if (useAuthStore.persist.hasHydrated()) {
      setLoading(false);
      return;
    }
    return useAuthStore.persist.onFinishHydration(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (serverUrl) {
      api.setBase(serverUrl);
      wsClient.setUrl(serverUrl);
    }
    api.setToken(deviceToken);
    wsClient.setToken(deviceToken);
  }, [deviceToken, serverUrl]);

  // Server says the session expired / password changed -> back to login.
  useEffect(() => {
    api.onUnauthorized(() => useAuthStore.getState().clearToken());
  }, []);

  if (loading) {
    return (
      <View style={[styles.loading, { backgroundColor: c.background }]}>
        <ActivityIndicator size="large" color={c.textSecondary} />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <NavigationContainer theme={navTheme}>
        <StatusBar style={c.isDark ? "light" : "dark"} />
        <Stack.Navigator screenOptions={{ headerShown: false }}>
          {!isAuthenticated ? (
            <Stack.Screen name="Login" component={LoginScreen} />
          ) : (
            <>
              <Stack.Screen name="Main" component={MainTabs} />
              <Stack.Screen name="FixtureDetail" component={FixtureDetailScreen} />
              <Stack.Screen name="Proposals" component={ProposalsScreen} />
              <Stack.Screen name="Panic" component={PanicScreen} />
            </>
          )}
        </Stack.Navigator>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
});

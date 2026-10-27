// Main app entry — Expo Router not used; simple navigator with screens

import "react-native-gesture-handler";
import { useEffect, useState } from "react";
import {
  View,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createStackNavigator } from "@react-navigation/stack";
import { LoginScreen } from "./screens/LoginScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { ProposalsScreen } from "./screens/ProposalsScreen";
import { PanicScreen } from "./screens/PanicScreen";
import { LimitsSettingsScreen } from "./screens/LimitsSettingsScreen";
import { useAuthStore } from "./store/index";
import { wsClient } from "./services/websocket";
import { api } from "./services/api";

const Stack = createStackNavigator();

export default function App() {
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
      <View style={styles.loading}>
        <ActivityIndicator size="large" />
      </View>
    );
  }

  return (
    <NavigationContainer>
      <Stack.Navigator screenOptions={{ headerShown: false }}>
        {!isAuthenticated ? (
          <Stack.Screen name="Login" component={LoginScreen} />
        ) : (
          <>
            <Stack.Screen name="Home" component={HomeScreen} />
            <Stack.Screen name="Proposals" component={ProposalsScreen} />
            <Stack.Screen name="Panic" component={PanicScreen} />
            <Stack.Screen name="LimitsSettings" component={LimitsSettingsScreen} />
          </>
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
});

// Jest config for mobile app
// Uses babel-jest for TypeScript transformation; no react-native preset required
// for pure-logic tests (utils, services, formatting).

module.exports = {
  testMatch: ["**/__tests__/**/*.test.[jt]s?(x)"],
  testEnvironment: "node",
  transform: {
    "^.+\\.(js|ts|tsx)$": ["babel-jest", { configFile: "./babel.config.js" }],
  },
  transformIgnorePatterns: [
    "node_modules/(?!(react-native|@react-native|react-native-reanimated|expo|@expo)/)",
  ],
  setupFiles: ["./jest.setup.js"],
  moduleNameMapper: {
    // Map react-native to react-native-web for node environment
    "^react-native$": "<rootDir>/node_modules/react-native",
  },
};

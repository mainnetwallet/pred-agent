// Jest config for mobile app

module.exports = {
  preset: "react-native",
  testMatch: ["**/__tests__/**/*.test.[jt]s?(x)"],
  testEnvironment: "node",
  transform: {
    "^.+\\.(js|ts|tsx)$": ["babel-jest", { configFile: "./babel.config.js" }],
  },
  transformIgnorePatterns: [
    "node_modules/(?!(react-native|@react-native|react-native-reanimated)/)",
  ],
  setupFiles: ["./jest.setup.js"],
};

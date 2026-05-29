import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      colors: {
        brand: {
          50: "#fef2f2",
          100: "#fee2e2",
          500: "#dc2626",
          600: "#b91c1c",
          700: "#991b1b",
        },
        surface: {
          DEFAULT: "#ffffff",
          dark: "#1a1a1a",
        },
        sidebar: {
          DEFAULT: "#ffffff",
          dark: "#121212",
        },
      },
    },
  },
  plugins: [],
};
export default config;

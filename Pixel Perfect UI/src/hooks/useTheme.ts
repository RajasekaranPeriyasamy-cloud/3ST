import { useSyncExternalStore } from "react";

import { getTheme, setTheme, subscribeTheme, toggleTheme, type ThemeMode } from "@/lib/theme";

/**
 * Read + write the app-wide theme. Backed by the module store in lib/theme.ts,
 * so every consumer (toggle, Plotly charts, Toaster) re-renders on one flip.
 */
export function useTheme(): {
  theme: ThemeMode;
  isDark: boolean;
  setTheme: (next: ThemeMode) => void;
  toggleTheme: () => void;
} {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, () => "dark" as ThemeMode);
  return { theme, isDark: theme === "dark", setTheme, toggleTheme };
}

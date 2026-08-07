import { useCallback, useEffect, useState } from "react";

import { applyTheme, resolveTheme, type ThemeMode } from "@/lib/theme";

export function useTheme() {
  const [theme, setThemeState] = useState<ThemeMode>(() => resolveTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: ThemeMode) => {
    setThemeState(next);
    applyTheme(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => {
      const next: ThemeMode = prev === "dark" ? "light" : "dark";
      applyTheme(next);
      return next;
    });
  }, []);

  return { theme, setTheme, toggleTheme };
}

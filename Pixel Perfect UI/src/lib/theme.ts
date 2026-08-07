export const THEME_STORAGE_KEY = "3st-theme";

export type ThemeMode = "light" | "dark";

/** Resolve persisted preference; default dark on first visit. */
export function resolveTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* ignore */
  }
  return "dark";
}

export function applyTheme(theme: ThemeMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* ignore */
  }
}

/** Inline boot script — runs before paint to avoid light→dark flash. */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem('${THEME_STORAGE_KEY}');if(t!=='light')document.documentElement.classList.add('dark');else document.documentElement.classList.remove('dark');}catch(e){document.documentElement.classList.add('dark');}})();`;

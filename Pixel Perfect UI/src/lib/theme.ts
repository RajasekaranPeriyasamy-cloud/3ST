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
  // Native scrollbars, form controls and the canvas behind the page follow this.
  document.documentElement.style.colorScheme = theme;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* ignore */
  }
}

/* ---------------------------------------------------------------------------
   Module-level store.

   The theme has to be a single shared value, not per-component state: the desk
   renders several Plotly charts that pick their paper/grid colours from
   `useTheme()`, and with one `useState` per hook instance the toggle flipped the
   <html> class while every chart kept rendering its old palette. `useTheme` is
   a `useSyncExternalStore` over this, so one toggle re-renders all of them.

   `subscribe` also listens to `storage`, so a second tab tracks the change.
   --------------------------------------------------------------------------- */

let current: ThemeMode = resolveTheme();
const listeners = new Set<() => void>();

function emit(): void {
  for (const fn of listeners) fn();
}

export function getTheme(): ThemeMode {
  return current;
}

export function setTheme(next: ThemeMode): void {
  if (next === current) {
    applyTheme(next); // keep the DOM honest even when the value is unchanged
    return;
  }
  current = next;
  applyTheme(next);
  emit();
}

export function toggleTheme(): void {
  setTheme(current === "dark" ? "light" : "dark");
}

export function subscribeTheme(listener: () => void): () => void {
  if (listeners.size === 0 && typeof window !== "undefined") {
    window.addEventListener("storage", onStorage);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== "undefined") {
      window.removeEventListener("storage", onStorage);
    }
  };
}

function onStorage(e: StorageEvent): void {
  if (e.key !== THEME_STORAGE_KEY) return;
  const next = e.newValue === "light" ? "light" : "dark";
  if (next === current) return;
  current = next;
  applyTheme(next);
  emit();
}

/** Inline boot script — runs before paint to avoid light→dark flash. */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem('${THEME_STORAGE_KEY}');var d=t!=='light';document.documentElement.classList.toggle('dark',d);document.documentElement.style.colorScheme=d?'dark':'light';}catch(e){document.documentElement.classList.add('dark');document.documentElement.style.colorScheme='dark';}})();`;

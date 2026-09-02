import { Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

type Props = {
  className?: string;
  /** Hide the "Light"/"Dark" word and render icon-only. */
  compact?: boolean;
};

/**
 * App-wide light/dark switch. Mounted once in the root header (top right), so it
 * is present on every desk — see src/routes/__root.tsx.
 */
export function ThemeToggle({ className, compact = false }: Props) {
  const { isDark, toggleTheme } = useTheme();
  const label = isDark ? "Switch to light theme" : "Switch to dark theme";

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className={cn(
        "report-no-print h-8 shrink-0 gap-1.5 px-2.5 text-xs",
        compact && "w-8 px-0",
        className,
      )}
      onClick={toggleTheme}
      title={label}
      aria-label={label}
      aria-pressed={isDark}
    >
      {isDark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
      {compact ? null : <span className="hidden sm:inline">{isDark ? "Light" : "Dark"}</span>}
    </Button>
  );
}

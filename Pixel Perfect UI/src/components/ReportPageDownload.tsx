import { Download } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Props = {
  /** Used as the browser print / Save-as-PDF document title. */
  title: string;
  className?: string;
};

/**
 * Opens the system print dialog (Save as PDF). Content that does not fit one
 * page continues on the next — see `@media print` rules in styles.css.
 */
export function ReportPageDownload({ title, className }: Props) {
  const [busy, setBusy] = useState(false);

  function handleDownload() {
    if (busy) return;
    setBusy(true);
    const prevTitle = document.title;
    const stamp = new Date()
      .toISOString()
      .slice(0, 16)
      .replace("T", "_")
      .replace(":", "");
    document.title = `${title.replace(/\s+/g, "_")}_${stamp}`;
    document.body.classList.add("printing-report");
    // Print/PDF must stay light-friendly even when Soft Dark Blue is active.
    const wasDark = document.documentElement.classList.contains("dark");
    if (wasDark) document.documentElement.classList.remove("dark");

    const cleanup = () => {
      document.title = prevTitle;
      document.body.classList.remove("printing-report");
      if (wasDark) document.documentElement.classList.add("dark");
      window.removeEventListener("afterprint", cleanup);
      setBusy(false);
    };
    window.addEventListener("afterprint", cleanup);
    // Fallback if afterprint is delayed/skipped (some Electron/WebView hosts)
    window.setTimeout(cleanup, 1500);
    window.print();
  }

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={cn("report-no-print shrink-0", className)}
      onClick={handleDownload}
      disabled={busy}
    >
      <Download className="mr-2 h-4 w-4" />
      {busy ? "Preparing…" : "Download PDF"}
    </Button>
  );
}

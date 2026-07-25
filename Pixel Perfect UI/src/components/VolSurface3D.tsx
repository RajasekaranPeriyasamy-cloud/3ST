import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";
import type { Data, Layout } from "plotly.js";

import type { VolSurfaceSnapshot } from "@/lib/types";

export default function VolSurface3D({ snap }: { snap: VolSurfaceSnapshot }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const data: Data[] = [
      {
        type: "surface",
        x: snap.strikes,
        y: snap.expiries.map((e) => e.dte),
        z: snap.z,
        colorscale: "Viridis",
        connectgaps: true,
        colorbar: { thickness: 12 },
        hovertemplate: "Strike %{x}<br>DTE %{y}d<br>IV %{z:.2f}%<extra></extra>",
      } as Data,
    ];

    const layout: Partial<Layout> = {
      autosize: true,
      height: 480,
      margin: { l: 0, r: 0, t: 10, b: 0 },
      paper_bgcolor: "rgba(0,0,0,0)",
      scene: {
        xaxis: { title: "Strike" },
        yaxis: { title: "Days to expiry" },
        zaxis: { title: "IV %" },
        camera: { eye: { x: 1.6, y: -1.6, z: 0.9 } },
      },
    };

    void Plotly.react(el, data, layout, { displayModeBar: false, responsive: true });

    return () => {
      Plotly.purge(el);
    };
  }, [snap]);

  return <div ref={ref} style={{ width: "100%", height: 480 }} />;
}

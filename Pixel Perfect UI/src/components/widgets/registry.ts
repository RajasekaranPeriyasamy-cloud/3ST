import type { ComponentType } from "react";

import type { WidgetId } from "@/context/AnalyticsDeskContext";
import { GammaDensityWidget } from "./GammaDensityWidget";
import { VolSurfaceWidget } from "./VolSurfaceWidget";
import { OiProfileWidget } from "./OiProfileWidget";
import { OiTrackerWidget } from "./OiTrackerWidget";
import { OiVarWidget } from "./OiVarWidget";

export interface WidgetDef {
  id: WidgetId;
  title: string;
  route: string;
  Component: ComponentType<{ expanded?: boolean }>;
  /** Shown in enable checklist for v1 */
  available: boolean;
}

export const WIDGET_CATALOG: WidgetDef[] = [
  {
    id: "oi-tracker",
    title: "OI Movers",
    route: "/oi-movers",
    Component: OiTrackerWidget,
    available: true,
  },
  {
    id: "oi-var",
    title: "OI VAR",
    route: "/oi-var",
    Component: OiVarWidget,
    available: true,
  },
  {
    id: "gamma-density",
    title: "Gamma Density",
    route: "/gamma-density",
    Component: GammaDensityWidget,
    available: true,
  },
  {
    id: "oi-profile",
    title: "OI Profile",
    route: "/oi-profile",
    Component: OiProfileWidget,
    available: true,
  },
  {
    id: "vol-surface",
    title: "Vol Surface",
    route: "/vol-surface",
    Component: VolSurfaceWidget,
    available: true,
  },
  {
    id: "vanna-exposure",
    title: "Vanna Exposure",
    route: "/vanna-exposure",
    Component: () => null,
    available: false,
  },
  {
    id: "iv-smile",
    title: "IV Smile",
    route: "/iv-smile",
    Component: () => null,
    available: false,
  },
  {
    id: "trade-suggestions",
    title: "Trade Suggestions",
    route: "/trade-suggestions",
    Component: () => null,
    available: false,
  },
];

export function getAvailableWidgets(): WidgetDef[] {
  return WIDGET_CATALOG.filter((w) => w.available);
}

export function getWidgetDef(id: WidgetId): WidgetDef | undefined {
  return WIDGET_CATALOG.find((w) => w.id === id);
}

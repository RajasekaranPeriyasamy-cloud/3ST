export type Segment = "equity" | "future" | "option";
export type Timeframe = "5min" | "15min" | "30min" | "60min";
export type SpreadTemplate =
  | "bull_call"
  | "bear_put"
  | "bear_call"
  | "bull_put"
  | "iron_condor";
export type Product = "underlying" | "options_spread";
export type StMethod = "heikin_ashi" | "regular" | "hybrid";
export type SystemMode = "Intraday" | "Positional";
export type RiskMode = "Off" | "%" | "Pts" | "ATR";

export interface StrategySettings {
  st_method: StMethod;
  system_mode: SystemMode;
  session_start: string;
  session_end: string;
  force_exit: string;
  atr1: number;
  factor1: number;
  atr2: number;
  factor2: number;
  atr3: number;
  factor3: number;
  st1_enabled: boolean;
  st2_enabled: boolean;
  st3_enabled: boolean;
  adx_enabled: boolean;
  adx_period: number;
  adx_threshold: number;
  sl_mode: RiskMode;
  sl_value: number;
  tgt_mode: RiskMode;
  tgt_value: number;
  tsl_mode: RiskMode;
  tsl_value: number;
}

export interface InstrumentHit {
  instrument_token: number;
  exchange: string;
  tradingsymbol: string;
  name: string;
  segment: string;
  instrument_type: string;
  lot_size: number;
  expiry?: string;
  strike?: number;
}

export interface SpreadLeg {
  tradingsymbol: string;
  exchange: string;
  instrument_token: number;
  side: "BUY" | "SELL";
  quantity: number;
  strike: number;
  option_type: "CE" | "PE";
  ltp?: number;
  premium?: number;
}

export interface SpreadConfig {
  underlying: string;
  expiry: string;
  long_template: SpreadTemplate;
  short_template: SpreadTemplate;
  width_steps: number;
  legs_long: SpreadLeg[];
  legs_short: SpreadLeg[];
}

export interface Selection extends StrategySettings {
  instrument_token: number | null;
  exchange: string | null;
  tradingsymbol: string | null;
  name?: string | null;
  segment: Segment;
  lot_size: number;
  timeframe: Timeframe;
  product: Product;
  spread: SpreadConfig | null;
}

export type WatchlistStatus = "waiting" | "triggered" | "active" | "closed";

export interface WatchlistItem extends Selection {
  id: string;
  status: WatchlistStatus;
  signal?: "long" | "short" | null;
  signal_at?: string | null;
  signal_note?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface SpreadPreview {
  legs_long: SpreadLeg[];
  legs_short: SpreadLeg[];
  net_debit?: number;
  net_credit?: number;
  max_loss?: number;
  max_profit?: number;
  lot_size?: number;
  spot?: number;
}

export interface HealthResponse {
  ok?: boolean;
  status?: string;
  configured?: boolean;
  kite_configured?: boolean;
  kite_authenticated?: boolean;
  index_options?: string[];
  timeframes?: Timeframe[];
}

export interface BacktestMetrics {
  net_pnl: number;
  net_points?: number;
  long_points?: number;
  short_points?: number;
  start_open?: number;
  end_close?: number;
  return_pct: number;
  trades: number;
  long_trades?: number;
  short_trades?: number;
  win_rate: number;
  profit_factor: number;
  max_drawdown?: number;
  max_drawdown_pct: number;
  avg_points?: number;
}

export interface EquityPoint {
  t: string;
  v: number;
}

export interface BacktestResult {
  meta?: {
    source?: string;
    start?: string;
    end?: string;
    bars?: number;
    max_days?: number;
    instrument?: string;
    timeframe?: string;
  };
  metrics: BacktestMetrics;
  equity: EquityPoint[];
  trades: Array<Record<string, unknown>>;
}

export interface BacktestLimits {
  source: string;
  timeframe: string;
  max_days: number;
  default_start: string;
  default_end: string;
  note?: string;
}

export type OiUnderlying = "NIFTY" | "BANKNIFTY" | "SENSEX";

export interface OiTrackerConfig {
  underlyings: OiUnderlying[];
  options_count: number;
  historical_minutes: number;
  intervals_min: number[];
  refresh_seconds: number;
  thresholds: Record<string, number>;
  alert_breach_ratio: number;
  risk_free_rate?: number;
}

export interface OiTrackerRow {
  key: string;
  strike: number;
  symbol: string;
  instrument_token?: number;
  position: number;
  latest_oi: number | null;
  oi_time: string | null;
  ltp?: number | null;
  iv?: number | null;
  pct: Record<string, number | null>;
  abs: Record<string, number | null>;
  iv_pct?: Record<string, number | null>;
  iv_abs?: Record<string, number | null>;
  signals?: Record<string, OiTrackerSignal | null>;
  breach: Record<string, boolean>;
}

export interface OiTrackerSignal {
  label: string;
  tone: "bull" | "bear" | "neutral";
  arrow: "up" | "down" | "flat";
}

export type OiBiasView = "long" | "short" | "sideways";

export interface OiBiasSide {
  view: OiBiasView;
  label: string;
  signal?: OiTrackerSignal | null;
}

export interface OiOverallBias {
  interval_min: number;
  strike_scope: string;
  sideways_threshold_pct: number;
  chain: OiBiasSide & {
    bull_pct: number;
    bear_pct: number;
    samples: number;
  };
  calls: OiBiasSide;
  puts: OiBiasSide;
}

export interface OiTrackerSnapshot {
  underlying: OiUnderlying;
  expiry: string;
  spot: number;
  atm_strike: number;
  spot_warning?: string | null;
  updated_at: string;
  intervals_min: number[];
  thresholds: Record<string, number>;
  options_count: number;
  calls: OiTrackerRow[];
  puts: OiTrackerRow[];
  pcr?: {
    chain_oi: number | null;
    call_oi_total: number;
    put_oi_total: number;
  };
  overall_bias?: OiOverallBias;
  alert: {
    triggered: boolean;
    call_breach_ratio: number;
    put_breach_ratio: number;
    breach_ratio_threshold: number;
  };
}

export interface OiLogEntry {
  at: string;
  event: string;
  detail: string;
  [key: string]: unknown;
}

export interface OiVarConfig {
  underlyings: OiUnderlying[];
  top_n: number;
  refresh_seconds: number;
}

export interface OiVarRow {
  side: "call" | "put";
  strike: number;
  symbol: string;
  instrument_token?: number;
  moneyness: string;
  oi: number;
  ltp: number;
  vwap: number;
  vwap_fallback?: boolean;
  delta_oi?: number | null;
  var_cr: number | null;
  var_chg_cr: number | null;
}

export interface OiVarFooter {
  var_cr_total: number | null;
  var_chg_total: number | null;
}

export interface OiVarSideTables {
  top_oi: OiVarRow[];
  top_chg: OiVarRow[];
  bottom_chg: OiVarRow[];
  footer: {
    top_oi: OiVarFooter;
    top_chg: OiVarFooter;
    bottom_chg: OiVarFooter;
  };
}

export interface OiVarSnapshot {
  underlying: OiUnderlying;
  expiry: string;
  spot: number;
  updated_at: string;
  baseline_date: string;
  chain_legs_quoted: number;
  chain_legs_total: number;
  top_n: number;
  calls: OiVarSideTables;
  puts: OiVarSideTables;
}

export type RollingUnderlying = "NIFTY" | "BANKNIFTY" | "SENSEX";
export type ReentryStyle = "zone_active" | "edge_only";
export type TradeMode = "Both" | "LongOnly" | "ShortOnly";

export interface RollingStraddleConfig extends StrategySettings {
  underlying: RollingUnderlying;
  expiry: string;
  entry_start: string;
  order_type: "MARKET" | "LIMIT";
  product: "MIS" | "NRML";
  tick_interval_sec: number;
  trade_mode: TradeMode;
  max_reentries_ce: number;
  max_reentries_pe: number;
  reentry_style: ReentryStyle;
  allow_dual_open: boolean;
  auto_start_on_boot: boolean;
}

export interface RollingLegState {
  status: "flat" | "open" | string;
  reentries_used: number;
  entries_today: number;
  tradingsymbol: string | null;
  exchange: string | null;
  strike: number | null;
  entry_price: number | null;
  entry_at: string | null;
  entry_order_id: string | null;
  last_action: string | null;
  blocked?: boolean;
  signal_strike?: number | null;
  signal_close?: number | null;
  signal_st1?: number | null;
  zone_exit_level?: number | null;
  zone_exit_label?: string | null;
  zone_exit_triggered?: boolean;
  short_ready?: boolean;
  short_entry?: boolean;
  ltp?: number | null;
}

export interface RollingStraddleState {
  runner: string;
  scheduler_running: boolean;
  morning_bar_seen: boolean;
  morning_bar_at: string | null;
  current_atm: number | null;
  prev_atm: number | null;
  last_roll_direction: string | null;
  last_spot: number | null;
  last_signal: string | null;
  last_signal_at: string | null;
  last_tick_at: string | null;
  ce: RollingLegState;
  pe: RollingLegState;
}

export interface RollingStraddleStatus {
  config: RollingStraddleConfig;
  state: RollingStraddleState;
  arm: { armed?: boolean; mode?: "paper" | "live"; note?: string };
  kite_authenticated?: boolean;
  scheduler?: { scheduler_alive?: boolean };
}

export interface RollingLogEntry {
  at: string;
  event: string;
  detail: string;
  [key: string]: unknown;
}

export interface SurvivorConfig {
  underlying: RollingUnderlying;
  expiry?: string;
  symbol_initials?: string;
  pe_gap: number;
  ce_gap: number;
  pe_quantity: number;
  ce_quantity: number;
  pe_symbol_gap?: number;
  ce_symbol_gap?: number;
  min_price_to_sell: number;
  sell_multiplier_threshold?: number;
  tick_interval_sec: number;
  product_type?: "NRML" | "MIS";
  tag?: string;
  auto_start_on_boot?: boolean;
}

export interface SurvivorState {
  runner: string;
  initialized?: boolean;
  last_tick_at?: string | null;
  last_spot?: number | null;
  nifty_pe_last_value?: number | null;
  nifty_ce_last_value?: number | null;
  last_error?: string | null;
}

export interface SurvivorStatus {
  config: SurvivorConfig;
  state: SurvivorState;
  arm: { armed?: boolean; mode?: "paper" | "live" };
  kite_authenticated?: boolean;
}

export interface SurvivorLogEntry {
  at: string;
  event: string;
  detail: string;
}

export interface WaveConfig {
  symbol_name: string;
  exchange: string;
  buy_gap: number;
  sell_gap: number;
  buy_quantity: number;
  sell_quantity: number;
  lot_size?: number;
  cool_off_time: number;
  product_type?: "NRML" | "MIS";
  order_type?: "LIMIT" | "MARKET";
  tag?: string;
  check_interval_sec: number;
  auto_start_on_boot?: boolean;
}

export interface WaveState {
  runner: string;
  initialized?: boolean;
  last_check_at?: string | null;
  last_spot?: number | null;
  active_orders?: number;
  last_error?: string | null;
}

export interface WaveStatus {
  config: WaveConfig;
  state: WaveState;
  arm: { armed?: boolean; mode?: "paper" | "live" };
  kite_authenticated?: boolean;
}

export interface WaveLogEntry {
  at: string;
  event: string;
  detail: string;
}

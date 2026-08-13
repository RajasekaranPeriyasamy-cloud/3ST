export type Segment = "equity" | "future" | "option";
export type Timeframe = "1min" | "3min" | "5min" | "15min" | "30min" | "60min";
export type EntryMode = "manual" | "signal";
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
  product_type?: "MIS" | "NRML";
  entry_mode: EntryMode;
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
  entry_side?: "BUY" | "SELL" | null;
  entry_price?: number | null;
  entry_qty?: number | null;
  entry_at?: string | null;
  trade_mode?: "paper" | "live" | null;
  order_ids?: string[];
  exit_label?: string | null;
  exit_line?: number | null;
  exit_reason?: string | null;
  exit_at?: string | null;
  exit_price?: number | null;
  last_signal_close?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface DeskPosition {
  exchange: string;
  tradingsymbol: string;
  instrument: string;
  product: string;
  quantity: number;
  average_price: number;
  last_price: number;
  pnl: number;
  change_pct: number;
  group_key: string;
}

export interface DeskPositionGroup {
  key: string;
  label: string;
  count: number;
  total_pnl: number;
  positions: DeskPosition[];
}

export interface PositionsView {
  mode: "paper" | "live";
  positions: DeskPosition[];
  groups: DeskPositionGroup[];
  total_pnl: number;
  count: number;
}

export type ActiveTradeStatus = "running" | "tracking" | "no_quote" | "no_position";

export interface ActiveTradeRow {
  id: string;
  tradingsymbol: string;
  exchange: string;
  signal?: "long" | "short" | null;
  trade_mode: "paper" | "live";
  entry_mode?: EntryMode;
  timeframe?: Timeframe;
  signal?: "long" | "short" | null;
  entry_side?: "BUY" | "SELL" | null;
  entry_price?: number | null;
  quantity: number;
  last_price?: number | null;
  pnl?: number | null;
  status: ActiveTradeStatus;
  exit_label?: string | null;
  exit_line?: number | null;
  st1?: number | null;
  st1_dir?: string | null;
  st1_exit_price?: number | null;
  st1_ltp_distance?: number | null;
  st1_exit_at_ltp?: boolean;
  st_exit_price?: number | null;
  st_exit_label?: string | null;
  st_exit_ltp_distance?: number | null;
  st_exit_at_ltp?: boolean;
  st_entry_price?: number | null;
  st_entry_label?: string | null;
  st_bear_exit?: number | null;
  st_bull_entry?: number | null;
  st_bands_live?: boolean;
  tsl_live?: number | null;
  trail_extreme?: number | null;
  entry_bar_close?: number | null;
  entry_bar_time?: string | null;
  signal_close?: number | null;
  st_method?: StMethod;
  price_divergence?: string | null;
  exit_note?: string | null;
  zone_exit_triggered?: boolean;
  risk_exit_triggered?: boolean;
  trail_stop?: number | null;
  target_level?: number | null;
  tsl_mode?: RiskMode | null;
  tsl_value?: number | null;
  force_exit?: string | null;
  session_end?: string | null;
  force_exit_due?: boolean;
  kite_product?: string | null;
  system_mode?: SystemMode;
  order_ids?: string[];
  entry_at?: string | null;
}

export interface ActiveTradesView {
  mode: "paper" | "live";
  trades: ActiveTradeRow[];
  count: number;
  orphans?: Array<{
    exchange: string;
    tradingsymbol: string;
    quantity: number;
    average_price?: number;
    product?: string;
    pnl?: number;
  }>;
  orphan_count?: number;
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

export type OiUnderlying =
  | "NIFTY"
  | "BANKNIFTY"
  | "SENSEX"
  | "CRUDEOIL"
  | "CRUDEOILM"
  | "NATURALGAS"
  | "GOLD"
  | "SILVER";

export interface OiTrackerConfig {
  underlyings: OiUnderlying[];
  options_count: number;
  historical_minutes: number;
  intervals_min: number[];
  refresh_seconds: number;
  thresholds: Record<string, number>;
  alert_breach_ratio: number;
  risk_free_rate?: number;
  bias_interval_min?: number;
  bias_sideways_threshold?: number;
  change_board_top_n?: number;
  change_board_interval_min?: number;
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
  prev_oi?: Record<string, number | null>;
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

export interface OiChangeBoardEntry {
  contract: string;
  strike: number;
  option_type: "CE" | "PE";
  expiry_label: string;
  prev_oi: number | null;
  /** open = session open OI; prev_close = prior-day closing OI */
  prev_oi_source?: "open" | "prev_close" | null;
  open_oi?: number | null;
  prev_close_oi?: number | null;
  curr_oi: number | null;
  abs_chg: number | null;
  pct_chg: number | null;
  bar_pct: number;
}

export interface OiChangeBoardSet {
  increase_abs: OiChangeBoardEntry[];
  increase_pct: OiChangeBoardEntry[];
  decrease_abs: OiChangeBoardEntry[];
  decrease_pct: OiChangeBoardEntry[];
}

export type OiChangeBoards = Record<string, OiChangeBoardSet>;

export interface OiMoversConfig {
  underlyings: OiUnderlying[];
  options_count: number;
  intervals_min: number[];
  refresh_seconds: number;
  change_board_top_n?: number;
  change_board_interval_min?: number;
  session_open_after?: string;
  mcx_underlyings?: OiUnderlying[];
}

export interface OiMoversHistoryPoint {
  t: string;
  ts_ms?: number;
  spot?: number | null;
  ce_oi?: number | null;
  pe_oi?: number | null;
  /** Flat Open/PD CE baseline (session open preferred). */
  ce_base_oi?: number | null;
  /** Flat Open/PD PE baseline (session open preferred). */
  pe_base_oi?: number | null;
  pcr?: number | null;
  base_source?: "open" | "prev_close" | string | null;
  source?: string;
}

/** Last good in-window CAS tick (process memory) for outside-window desk display. */
export interface CasIndicativeLast {
  indicative: number | null;
  spot?: number | null;
  reference_limit_price?: number | null;
  upper_circuit_limit?: number | null;
  lower_circuit_limit?: number | null;
  total_imbalance?: number | null;
  source?: string;
  asof?: string;
}

/** ATM synthetic future from nearest-expiry CE/PE (display-only on CAS desk). */
export interface SyntheticFuture {
  F: number;
  atm_strike: number;
  expiry: string;
  ce_symbol?: string;
  pe_symbol?: string;
  ce_price?: number;
  pe_price?: number;
  ce_source?: "mid" | "ltp" | string;
  pe_source?: "mid" | "ltp" | string;
  price_source?: "mid" | "ltp" | "mixed" | string;
  spot?: number | null;
  basis_vs_spot?: number | null;
  basis_vs_indicative?: number | null;
  asof?: string;
}

/** Proxy / constituent components for the desk pre-close forecast. */
export interface CasEstimateComponents {
  synth_f?: number | null;
  fut_ltp?: number | null;
  ref_vwap?: number | null;
  fut_poc?: number | null;
  fut_symbol?: string | null;
  ref_vwap_source?: string | null;
  /** pre_close_1515 | running_1500 | session */
  ref_vwap_window?: string | null;
  weights?: Record<string, number>;
  weights_used?: Record<string, number>;
  clamped?: boolean;
  clamp_low?: number | null;
  clamp_high?: number | null;
  constituent?: Record<string, unknown> | null;
}

/** Why `official_indicative` came back null (null itself means "accepted"). */
export type CasOfficialRejectReason =
  | "outside_window"
  | "no_quote"
  | "missing_field"
  | "no_spot_anchor"
  | "out_of_band";

/** One recorded CAS poll — the chart series and the future calibration set. */
export interface CasHistoryPoint {
  ts: string;
  session: string;
  underlying: string;
  in_cas_window: boolean;
  spot: number | null;
  official_indicative: number | null;
  official_raw: number | null;
  official_reject_reason: CasOfficialRejectReason | null;
  estimate: number | null;
  estimate_method: string | null;
  synth_f: number | null;
  fut_ltp: number | null;
  ref_vwap: number | null;
  ref_vwap_window: string | null;
  fut_poc: number | null;
  total_imbalance: number | null;
  /** Null until the Phase B constituent rebuild lands. */
  constituent_est: number | null;
  coverage: number | null;
  source: string | null;
}

export interface CasHistoryResponse {
  underlying: string;
  session: string | null;
  count: number;
  sessions: string[];
  series: CasHistoryPoint[];
}

export interface CasIndicative {
  underlying: string;
  in_cas_window: boolean;
  spot: number | null;
  /** Sanitized official Kite/NSE indicative (null when missing or fails spot sanity). */
  indicative: number | null;
  /** Same as indicative — sanitized official only. */
  official_indicative?: number | null;
  /** What Kite actually sent, before sanitization — for diagnosing a blank official. */
  official_raw?: number | null;
  /** Null when accepted; else why the official value was dropped. */
  official_reject_reason?: CasOfficialRejectReason | null;
  /** Desk pre-close forecast / proxy (primary hero when official is null). Not official CAS. */
  estimate?: number | null;
  estimate_components?: CasEstimateComponents | null;
  estimate_method?: "proxy_v1" | "constituent_v1" | string | null;
  reference_limit_price?: number | null;
  upper_circuit_limit?: number | null;
  lower_circuit_limit?: number | null;
  total_imbalance?: number | null;
  source: "kite_quote" | "unavailable" | string;
  asof: string;
  /** Present when API process saw a prior in-window indicative for this underlying. */
  last?: CasIndicativeLast | null;
  /** All-day futures volume POC (same helper as Gamma / OI Movers). */
  session_poc?: SessionPoc | null;
  /** Nearest-expiry ATM synth F = K + CE − PE. */
  synthetic_future?: SyntheticFuture | null;
}

export interface SessionPoc {
  poc: number;
  fut_symbol?: string;
  fut_token?: number;
  bin_step?: number;
  total_volume?: number;
  asof?: string;
  path?: Array<{ t: string; close: number; ts_ms?: number }>;
}

/** Why `session_poc` is null, so a blank Fut POC can explain itself. */
export type SessionPocReason =
  | "unknown_underlying"
  | "future_unresolved"
  | "before_session_open"
  | "fetch_failed"
  | "no_session_bars"
  | "no_session_volume";

export interface SessionPocStatus {
  ok: boolean;
  reason?: SessionPocReason | "error" | null;
}

export interface OiMoversSnapshot {
  underlying: string;
  expiry: string;
  spot: number;
  atm_strike: number;
  spot_warning?: string | null;
  updated_at: string;
  intervals_min: Array<number | string>;
  options_count?: number;
  change_boards?: OiChangeBoards;
  change_board_top_n?: number;
  change_board_interval_min?: number | string;
  change_basis?: string;
  pcr?: {
    chain_oi: number | null;
    call_oi_total: number;
    put_oi_total: number;
  };
  ce_oi?: number;
  pe_oi?: number;
  ce_base_oi?: number | null;
  pe_base_oi?: number | null;
  base_source?: "open" | "prev_close" | string | null;
  history?: OiMoversHistoryPoint[];
  chart_series?: OiMoversHistoryPoint[];
  baseline?: {
    prefer: string;
    open_count: number;
    prev_close_count: number;
    total: number;
  };
  cas?: CasIndicative | null;
  session_poc?: SessionPoc | null;
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
  change_boards?: OiChangeBoards;
  change_board_top_n?: number;
  change_board_interval_min?: number;
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
  dvar_modes?: Array<"oi_mark" | "true">;
  dvar_mode?: "oi_mark" | "true";
  strike_window?: number;
  min_oi?: number;
  multi_expiry_count?: number;
  alert_dvar_burst_cr?: number;
}

export interface OiVarRow {
  side: "call" | "put";
  strike: number;
  symbol: string;
  instrument_token?: number;
  moneyness: string;
  oi: number;
  ltp: number;
  price?: number;
  price_source?: string;
  open?: number | null;
  ltp_chg?: number | null;
  ltp_arrow?: "up" | "down" | "flat";
  delta_oi?: number | null;
  var_cr: number | null;
  var_arrow?: "up" | "down" | "flat";
  var_chg_cr: number | null;
  var_chg_arrow?: "up" | "down" | "flat";
  var_chg_session?: number | null;
  flow_tag?: string;
  pct_side_var?: number | null;
  near_call_wall?: boolean;
  near_put_wall?: boolean;
  near_flip?: boolean;
}

export interface OiVarFooter {
  var_cr_total: number | null;
  var_chg_total: number | null;
}

export interface OiVarSideTables {
  top_oi: OiVarRow[];
  top_chg: OiVarRow[];
  bottom_chg: OiVarRow[];
  top_var?: OiVarRow[];
  top_dvar_up?: OiVarRow[];
  top_dvar_dn?: OiVarRow[];
  footer: {
    top_oi: OiVarFooter;
    top_chg: OiVarFooter;
    bottom_chg: OiVarFooter;
    top_var?: OiVarFooter;
    top_dvar_up?: OiVarFooter;
    top_dvar_dn?: OiVarFooter;
  };
}

export interface OiVarSummary {
  ce_var_total: number | null;
  pe_var_total: number | null;
  pcr_var: number | null;
  ce_dvar_total: number | null;
  pe_dvar_total: number | null;
  net_dvar: number | null;
  concentration?: {
    ce_top_share_pct: number | null;
    pe_top_share_pct: number | null;
  };
}

export interface OiVarProfilePoint {
  strike: number;
  ce_var: number;
  pe_var: number;
  net_dvar: number;
  total_var: number;
}

export interface OiVarHistoryPoint {
  t: string;
  ts_ms?: number;
  spot: number;
  ce_var_total: number | null;
  pe_var_total: number | null;
  net_dvar: number | null;
  top_ce_strike: number | null;
  top_pe_strike: number | null;
  pcr_var: number | null;
  ce_flow_regime?: "long" | "short" | "mixed" | null;
  pe_flow_regime?: "long" | "short" | "mixed" | null;
  ce_flow_score?: number | null;
  pe_flow_score?: number | null;
}

export interface OiVarFlowRegimeSide {
  side: "CE" | "PE";
  regime: "long" | "short" | "mixed";
  score: number;
  long_weight: number;
  short_weight: number;
  counts?: Record<string, number>;
}

export interface OiVarFlowShift {
  side: "CE" | "PE";
  from_regime: string;
  to_regime: string;
  t?: string | null;
  spot?: number | null;
  label?: string;
  message?: string;
}

export interface OiVarAlert {
  type: string;
  message: string;
  severity: string;
  side?: string;
  from_regime?: string;
  to_regime?: string;
  t?: string;
}

export interface OiVarSnapshot {
  underlying: OiUnderlying;
  expiry: string;
  spot: number;
  atm_strike?: number | null;
  updated_at: string;
  baseline_date: string;
  dvar_mode?: string;
  session_open_at?: string | null;
  chain_legs_quoted: number;
  chain_legs_total: number;
  top_n: number;
  price_source_stats?: Record<string, number>;
  flow_regime?: {
    ce: OiVarFlowRegimeSide;
    pe: OiVarFlowRegimeSide;
  };
  flow_shifts?: OiVarFlowShift[];
  summary?: OiVarSummary;
  gamma_context?: {
    available: boolean;
    call_wall: number | null;
    put_wall: number | null;
    flip_level: number | null;
  };
  var_profile?: OiVarProfilePoint[];
  multi_expiry?: Array<{
    expiry: string;
    ce_var_total: number | null;
    pe_var_total: number | null;
    top_ce_strike: number | null;
    top_ce_var: number | null;
    top_pe_strike: number | null;
    top_pe_var: number | null;
    legs?: number;
  }>;
  history?: OiVarHistoryPoint[];
  alerts?: OiVarAlert[];
  calls: OiVarSideTables;
  puts: OiVarSideTables;
}

export interface GammaConfig {
  underlyings: OiUnderlying[];
  refresh_seconds: number;
  strike_window: number;
  risk_free_rate: number;
  dividend_yield?: number;
  sign_modes?: Array<"naive" | "customer" | "oi_delta">;
  sign_mode?: "naive" | "customer" | "oi_delta";
  hedge_moves_pts?: number[];
  multi_expiry_count?: number;
  concentration_summary_window?: number;
  concentration_summary_refresh_seconds?: number;
  concentration_summary_underlyings?: OiUnderlying[];
  provider?: string;
  requires_session?: boolean;
}

export interface GammaConcentrationSummaryItem {
  underlying: OiUnderlying | string;
  expiry: string | null;
  spot: number | null;
  hhi: number | null;
  band: GammaConcentrationBand | null;
  band_label?: string | null;
  mass_basis?: GammaMassBasis | string | null;
  pin_strike: number | null;
  cliff_strike: number | null;
  gini?: number | null;
  shape_quadrant?: string | null;
  hhi_percentile_30d?: number | null;
  hhi_session_count?: number | null;
  source?: "live" | "history" | "error" | string | null;
  error?: string | null;
}

export interface GammaConcentrationSummary {
  underlyings: string[];
  strike_window: number;
  updated_at: string;
  provider?: string;
  items: GammaConcentrationSummaryItem[];
}

export interface GammaStrikeRow {
  strike: number;
  ce_oi: number;
  pe_oi: number;
  ce_density: number;
  pe_density: number;
  total_density: number;
  ce_gex: number;
  pe_gex: number;
  net_gex: number;
  ce_iv: number | null;
  pe_iv: number | null;
  magnet?: number;
  ce_price_source?: string | null;
  pe_price_source?: string | null;
  ce_oi_base?: number | null;
  pe_oi_base?: number | null;
  ce_doi?: number | null;
  pe_doi?: number | null;
  ce_oi_base_source?: "open" | "prev_close" | null;
  pe_oi_base_source?: "open" | "prev_close" | null;
}

export interface GammaExpectedMove {
  sigma1_up: number;
  sigma1_dn: number;
  sigma2_up: number;
  sigma2_dn: number;
  sigma1_pts: number;
  source?: "straddle" | "atm_iv" | string;
  straddle_pts?: number | null;
}

export interface GammaConvexityZone {
  strike: number;
  total_density: number;
  net_gex: number;
  magnet?: number | null;
}

export interface GammaGexProfilePoint {
  spot: number;
  gex: number;
  gex_cr: number;
}

export interface GammaHedgeFlow {
  move_pts: number;
  delta_units: number;
  futures_lots: number;
  direction: "dealers_buy" | "dealers_sell" | "flat" | string;
  notional_cr: number;
}

export interface GammaMultiExpiry {
  expiry: string;
  tte_years: number;
  total_gex: number;
  flip_level: number | null;
  legs?: number;
  weight?: number | null;
}

export interface GammaHistoryPoint {
  t: string;
  spot: number | null;
  total_gex: number | null;
  /** Sum of +VE leg GEX (plotted green, Cr). */
  pos_gex?: number | null;
  /** Absolute sum of −VE leg GEX (plotted red, Cr). */
  neg_gex?: number | null;
  /** Minute candle volume when row comes from spot path. */
  volume?: number | null;
  /** ATM IV % when the history tick carried it (sparse on chart_series). */
  atm_iv?: number | null;
  flip_level: number | null;
  gamma_regime?: string | null;
  hhi?: number | null;
  conviction?: number | null;
  pin_strike?: number | null;
  ts_ms?: number;
  source?: string;
}

export interface GammaReversal {
  t: string | null;
  ts_ms?: number | null;
  /** First TF bar where min_move cleared (IST ISO), not wall-clock accept time. */
  confirmed_at?: string | null;
  confirmed_ts_ms?: number | null;
  spot: number;
  side: "bullish" | "bearish";
  move_pts: number;
  gex_confirm: boolean;
  /** True when pivot is before mid-session GEX recording (shown muted, no GEX gate). */
  partial_ungated?: boolean;
  /** Live: price pivot shown before GEX/OI hard gate; muted until promoted. */
  provisional?: boolean;
  oi_align?: boolean;
  /** Hard OI gate: true=supportive, false=hostile/unsupported, null=oi_unknown. */
  oi_gate_pass?: boolean | null;
  tf?: "1m" | "5m" | "15m" | string;
  label: string;
}

export interface GammaVannaStrip {
  total_vex_cr: number;
  vanna_regime: string;
  joint_read: string;
}

export type GammaConcentrationBand = "concentrated" | "mixed" | "diffuse";

export interface GammaTopContributor {
  strike: number;
  share: number;
  /** share² — this strike's own contribution to the HHI. */
  share_sq?: number;
  net_gex: number;
  /** |CE γ| + |PE γ| at this strike (gross dealer gamma). */
  gross_gex?: number;
  side_bias: "call" | "put" | "mixed" | string;
}

/** Per-strike mass used by the concentration index. */
export type GammaMassBasis = "gross" | "net";

export interface GammaDailyHhiPoint {
  date: string;
  hhi: number;
  band: GammaConcentrationBand;
}

export interface GammaConcentration {
  hhi: number | null;
  /** HHI of |CE γ| + |PE γ| shares — "where dealer gamma clusters". */
  hhi_gross?: number | null;
  /** HHI of |CE γ + PE γ| shares — concentration of the net dealer imbalance. */
  hhi_net?: number | null;
  /** Which of the two `hhi` echoes. */
  mass_basis?: GammaMassBasis | string | null;
  /** Band cut points for the active basis (gross defaults: 0.18 / 0.08). */
  band_cut_compressed?: number | null;
  band_cut_balanced?: number | null;
  top1_share: number | null;
  top3_share: number | null;
  top5_share?: number | null;
  effective_strikes: number | null;
  band: GammaConcentrationBand | null;
  /** Desk vocabulary for `band`: compressed / balanced / dispersed. */
  band_label?: string | null;
  dominant_strike: number | null;
  dominant_share: number | null;
  pin_strike: number | null;
  pin_share: number | null;
  pin_stable: boolean | null;
  pin_stability_pct: number | null;
  call_hhi?: number | null;
  put_hhi?: number | null;
  call_band?: GammaConcentrationBand | null;
  put_band?: GammaConcentrationBand | null;
  /** Strike holding the most dealer long gamma (max +net_gex). */
  pos_gamma_peak_strike?: number | null;
  /** Strike holding the most dealer short gamma (min −net_gex). */
  neg_gamma_peak_strike?: number | null;
  /** Strike-level Gini of |GEX| shares (inequality; not concentration). */
  gini?: number | null;
  call_gini?: number | null;
  put_gini?: number | null;
  /** Ávila HHI×Gini label, e.g. unequal-dispersed. */
  shape_quadrant?: string | null;
  top_contributors?: GammaTopContributor[];
  cliff_strike?: number | null;
  hhi_session_mean?: number | null;
  hhi_percentile_intraday?: number | null;
  /** Percentile of current HHI among last ~30 trading-day (session) HHIs. */
  hhi_percentile_30d?: number | null;
  /** Number of trading days in the 30d percentile sample. */
  hhi_session_count?: number | null;
  /**
   * Day-end HHI per trading session (oldest → newest, ≤30), filtered to the
   * current measurement basis. Today's value is the last row.
   */
  daily_hhi?: GammaDailyHhiPoint[];
  /** Previous session's day-end HHI (excludes today). */
  hhi_prev_session?: number | null;
  hhi_prev_session_date?: string | null;
  /** Percent change vs the previous session. */
  hhi_dod_pct?: number | null;
  /** Mean of the last 5 *prior* sessions (today excluded). */
  hhi_mean_5?: number | null;
  hhi_mean_5_band?: GammaConcentrationBand | null;
  hhi_mean_30?: number | null;
  /** Percent change vs the 5-session mean. */
  hhi_vs_mean_pct?: number | null;
  hhi_low_30?: number | null;
  hhi_high_30?: number | null;
  /**
   * Sessions in the sample whose strike window was inferred, not recorded —
   * rows written before basis tagging existed.
   */
  hhi_session_assumed_count?: number | null;
}

export interface GammaConviction {
  score: number | null;
  delta: number | null;
  direction: "rising" | "falling" | "flat" | string;
}

export interface GammaMarketRead {
  regime_line: string;
  vol_line: string;
  shape_line: string;
  change_line: string;
  levels_line: string;
}

export interface GammaMomentumComponents {
  gex: number;
  squeeze: number;
  oi_flow: number;
  iv: number;
  structure: number;
}

export interface GammaMomentum {
  score: number;
  label: "bullish" | "neutral" | "bearish" | string;
  components: GammaMomentumComponents;
  drivers: string[];
}

export interface GammaReferenceLevels {
  prev_day_high?: number | null;
  prev_day_low?: number | null;
  prev_day_close?: number | null;
  prev_week_high?: number | null;
  prev_week_low?: number | null;
  prev_week_close?: number | null;
}

export interface GammaSnapshot {
  underlying: OiUnderlying;
  expiry: string;
  /** Calendar days to expiry; 0 on expiry day. */
  dte?: number | null;
  spot: number;
  updated_at: string;
  tte_years: number;
  atm_strike: number;
  atm_iv: number | null;
  total_gex: number;
  total_gex_cr?: number;
  pos_gex?: number;
  neg_gex?: number;
  pos_gex_cr?: number;
  neg_gex_cr?: number;
  gamma_regime: "positive" | "negative";
  sign_mode?: string;
  dividend_yield?: number;
  risk_free_rate?: number;
  price_source_stats?: Record<string, number>;
  flip_level: number | null;
  flip_sticky_delta?: number | null;
  flip_crossings?: number[];
  distance_to_flip?: number | null;
  flip_slope?: number | null;
  gex_profile?: GammaGexProfilePoint[];
  call_wall: number | null;
  put_wall: number | null;
  call_wall_magnet?: number | null;
  put_wall_magnet?: number | null;
  expected_move: GammaExpectedMove | null;
  hedge_flow?: GammaHedgeFlow[];
  multi_expiry?: GammaMultiExpiry[];
  multi_expiry_gex?: number;
  primary_weight?: number;
  vanna_strip?: GammaVannaStrip | null;
  concentration?: GammaConcentration | null;
  conviction?: GammaConviction | null;
  momentum?: GammaMomentum | null;
  market_read?: GammaMarketRead | null;
  reference_levels?: GammaReferenceLevels | null;
  history?: GammaHistoryPoint[];
  chart_series?: GammaHistoryPoint[];
  reversals?: GammaReversal[];
  /**
   * True when Require GEX was ON but the gate was relaxed / hybrid:
   * Research+sparse, or mid-session GEX recording (partial history).
   */
  reversals_gex_relaxed?: boolean;
  /** True when Require GEX was ON, Live mode, complete history still sparse — GEX confirm waits; provisional pivots may still show. */
  reversals_gex_waiting?: boolean;
  /** Usable session GEX ticks counted toward the min-samples gate. */
  reversals_gex_samples?: number;
  /** Min usable GEX ticks before the hard gate applies (default 5). */
  reversals_gex_min_samples?: number;
  /** Echo of sparse-GEX policy: live | research. */
  reversal_gex_mode?: "live" | "research" | string;
  /** ISO timestamp of the first persisted GEX sample today (if any). */
  gex_history_started_at?: string | null;
  /** True when first GEX sample is well after session open (or history empty). */
  gex_history_partial?: boolean;
  /** Count of usable session GEX history ticks. */
  gex_history_points?: number;
  chain_legs_quoted: number;
  chain_legs_total: number;
  strike_window: number;
  convexity_zones: GammaConvexityZone[];
  strikes: GammaStrikeRow[];
  provider?: string;
  oi_baseline_mode?: "session_open" | "prev_close" | string;
  oi_baseline_note?: string | null;
  oi_baseline_open_count?: number;
  oi_baseline_prev_close_count?: number;
  cas?: CasIndicative | null;
  session_poc?: SessionPoc | null;
  /** Always present; explains a null `session_poc` rather than hiding the chip. */
  session_poc_status?: SessionPocStatus | null;
}

export interface VannaConfig {
  underlyings: OiUnderlying[];
  refresh_seconds: number;
  strike_window: number;
  risk_free_rate: number;
  iv_shock_vol_points: number[];
  provider?: string;
  requires_session?: boolean;
  sign_convention?: string;
  note?: string;
}

export interface VannaStrikeRow {
  strike: number;
  ce_oi: number;
  pe_oi: number;
  ce_density: number;
  pe_density: number;
  total_density: number;
  ce_vex_raw: number;
  pe_vex_raw: number;
  net_vex_raw: number;
  ce_vex_inr: number;
  pe_vex_inr: number;
  net_vex_inr: number;
  ce_iv: number | null;
  pe_iv: number | null;
}

export interface VannaIvShock {
  vol_points: number;
  delta_shares: number;
  notional_inr: number;
  notional_cr: number;
  direction: "dealers_buy_delta" | "dealers_sell_delta" | "flat" | string;
}

export interface VannaRecommendation {
  id: string;
  structure: string;
  title: string;
  bias?: string;
  strikes_focus?: number[];
  score?: number;
  reasoning: string;
  pricing_hint?: string;
  disclaimer?: string;
  underlying?: string;
  expiry?: string;
  vex_context?: {
    regime?: string;
    vanna_line?: number | null;
    spot?: number;
    call_wall?: number | null;
    put_wall?: number | null;
    total_vex_cr?: number;
    shock_1?: VannaIvShock | null;
  };
}

export interface VannaSnapshot {
  underlying: OiUnderlying;
  expiry: string;
  spot: number;
  updated_at: string;
  tte_years: number;
  atm_strike: number;
  atm_iv: number | null;
  total_vex_raw: number;
  total_vex_inr: number;
  total_vex_cr: number;
  vanna_regime: "positive" | "negative" | string;
  vanna_line: number | null;
  call_wall: number | null;
  put_wall: number | null;
  iv_shocks: VannaIvShock[];
  chain_legs_quoted: number;
  chain_legs_total: number;
  strike_window: number;
  strikes: VannaStrikeRow[];
  recommendations?: VannaRecommendation[];
}

/** Higher-order Greeks desk strike row (GEX/VEX + charm/vanna/speed). */
export interface GreeksStrikeRow {
  strike: number;
  ce_oi: number;
  pe_oi: number;
  ce_iv: number | null;
  pe_iv: number | null;
  ce_gex: number;
  pe_gex: number;
  net_gex: number;
  ce_vex_inr: number;
  pe_vex_inr: number;
  net_vex_inr: number;
  ce_charm: number;
  pe_charm: number;
  net_charm: number;
  ce_vanna: number;
  pe_vanna: number;
  net_speed: number;
  net_vomma: number;
  net_color: number;
  net_zomma: number;
  vanna_gamma_score: number;
  ce_delta?: number | null;
  pe_delta?: number | null;
}

export interface TradeSuggestionLeg {
  side: "buy" | "sell" | string;
  option_type: "CE" | "PE" | string;
  strike: number;
  tenor?: string;
}

export interface TradeSuggestionRisk {
  max_risk?: number | null;
  max_return?: number | null;
  max_risk_inr?: number | null;
  max_return_inr?: number | null;
  pop?: number | null;
  breakevens?: number[] | null;
  net_premium?: number | null;
  width?: number;
  lot_size?: number;
  net_delta?: number;
  net_gamma?: number;
  net_vega?: number;
  net_theta?: number;
  theta_vega_ratio?: number;
  note?: string;
}

export interface TradeSuggestion {
  id: string;
  structure: string;
  title: string;
  bias?: string;
  category?: string;
  score?: number;
  reasoning: string;
  legs: TradeSuggestionLeg[];
  risk_profile: TradeSuggestionRisk;
  adjustment_rules?: string[];
  strikes_focus?: number[];
  pricing_hint?: string;
  disclaimer?: string;
  underlying?: string;
  expiry?: string;
  spot?: number;
  atm_strike?: number;
  context?: Record<string, unknown>;
}

export interface TradeSuggestionsConfig {
  underlyings: OiUnderlying[];
  refresh_seconds: number;
  strike_window: number;
  max_ideas: number;
  provider?: string;
  requires_session?: boolean;
  risk_free_rate?: number;
  dividend_yield?: number;
  theta_mode?: string;
  disclaimer?: string;
  note?: string;
}

export interface TradeSuggestionsSnapshot {
  underlying: OiUnderlying | string;
  expiry: string;
  provider?: string;
  spot: number;
  atm_strike: number;
  atm_iv: number | null;
  updated_at: string;
  tte_years?: number;
  weekend_bleed_window?: boolean;
  regimes: {
    gamma?: string;
    vanna?: string;
    iv_flow?: string;
    vol?: string;
  };
  signals?: {
    gamma?: string;
    charm?: string;
    charm_detail?: string;
    vanna?: string;
    vanna_detail?: string;
  };
  charm_sides?: {
    net?: number;
    call?: number;
    put?: number;
    peak_strike?: number | null;
  };
  vanna_sides?: {
    net_cr?: number;
    peak_strike?: number | null;
    delta_from_1pt_iv_cr?: number;
  };
  levels: {
    flip_level?: number | null;
    dynamic_flip_level?: number | null;
    vanna_line?: number | null;
    call_wall?: number | null;
    put_wall?: number | null;
    pin_level?: number | null;
  };
  portfolio_greeks: {
    net_delta?: number;
    net_gamma?: number;
    net_vega?: number;
    net_theta?: number;
    total_speed?: number;
    total_vomma?: number;
    total_charm?: number;
    total_color?: number;
    total_zomma?: number;
    total_gex?: number;
    total_vex_cr?: number;
  };
  hot_zones?: Array<{
    strike: number;
    vanna_gamma_score: number;
    net_gex: number;
    net_vex_inr: number;
    net_speed: number;
    net_vomma: number;
  }>;
  suggestions: TradeSuggestion[];
  greeks_snapshot?: {
    strikes?: GreeksStrikeRow[];
    gex_vex?: Record<string, unknown>;
  };
  gamma_context?: {
    total_gex?: number;
    flip_level?: number | null;
    expected_move?: GammaExpectedMove | null;
  } | null;
  vanna_context?: {
    total_vex_cr?: number;
    vanna_line?: number | null;
    iv_shocks?: VannaIvShock[];
  } | null;
  disclaimer?: string;
  note?: string;
}

export interface VolSurfaceConfig {
  underlyings: OiUnderlying[];
  strike_count: number;
  max_expiries: number;
  refresh_seconds: number;
}

export interface VolSurfaceExpiry {
  expiry: string;
  dte: number;
  tte_years: number;
  atm_iv: number | null;
}

export interface VolSurfacePoint {
  expiry: string;
  dte: number;
  strike: number;
  moneyness: number;
  iv: number;
  option_type: "CE" | "PE";
}

export interface VolSurfaceTermPoint {
  expiry: string;
  dte: number;
  atm_iv: number | null;
}

export interface VolSurfaceSnapshot {
  underlying: OiUnderlying;
  spot: number;
  atm_strike: number;
  strike_step: number;
  strike_count: number;
  updated_at: string;
  strikes: number[];
  moneyness: number[];
  expiries: VolSurfaceExpiry[];
  z: (number | null)[][];
  points: VolSurfacePoint[];
  legs_resolved: number;
  term_structure: VolSurfaceTermPoint[];
}

export interface IvSmileConfig {
  underlyings: OiUnderlying[];
  strike_count: number;
  refresh_seconds: number;
}

export interface IvSmilePoint {
  strike: number;
  ce_iv: number | null;
  pe_iv: number | null;
}

export interface IvSmileSnapshot {
  underlying: OiUnderlying;
  expiry: string;
  spot: number;
  atm_strike: number;
  atm_iv: number | null;
  skew: number | null;
  chain: IvSmilePoint[];
  updated_at: string;
}

export interface IvSkewConfig {
  underlyings: OiUnderlying[];
  max_expiries: number;
  target_delta: number;
  refresh_seconds: number;
  wing_delta: number;
}

export interface IvSkewPoint {
  strike: number;
  abs_delta: number;
  iv: number | null;
  option_type: "CE" | "PE";
}

/** How the 25Δ IV was obtained. */
export type IvSkewQuality = "interpolated" | "extrapolated" | "unavailable";

/** Whether the chain underneath was good enough to believe it. */
export type IvSkewConfidence = "clean" | "degraded" | "unavailable";

export interface IvSkewExpiry {
  expiry: string;
  dte: number;
  ok: boolean;
  quality: IvSkewQuality;
  confidence: IvSkewConfidence;
  warnings: string[];
  error?: string;
  half_width?: number | null;
  forward?: number | null;
  // Present only on resolved rows.
  tte_years?: number;
  forward_basis?: number;
  forward_spread_bps?: number;
  atm_strike?: number | null;
  atm_iv?: number | null;
  atm_parity_gap?: number | null;
  call_iv?: number | null;
  put_iv?: number | null;
  call_quality?: IvSkewQuality;
  put_quality?: IvSkewQuality;
  call_delta_range?: [number, number] | null;
  put_delta_range?: [number, number] | null;
  call_bracket_gap?: number | null;
  put_bracket_gap?: number | null;
  risk_reversal?: number | null;
  butterfly?: number | null;
  legs_resolved?: number;
  legs_dropped?: Record<string, number>;
  points?: IvSkewPoint[];
}

export interface IvSkewSnapshot {
  underlying: OiUnderlying;
  label: string;
  exchange: string;
  reference: number;
  reference_source: string;
  strike_step: number;
  target_delta: number;
  expiries: IvSkewExpiry[];
  updated_at: string;
}

export interface IvSkewDailyPoint {
  date: string;
  underlying: OiUnderlying;
  expiry: string;
  rank: number;
  dte: number | null;
  rr: number | null;
  fly: number | null;
  atm_iv: number | null;
  call_iv: number | null;
  put_iv: number | null;
  forward_basis: number | null;
  parity_gap: number | null;
  confidence: IvSkewConfidence;
  quality: IvSkewQuality;
  reference: number | null;
  ts: string | null;
  samples: number;
}

export interface IvSkewDailySeries {
  underlying: OiUnderlying;
  rank: number;
  clean_only: boolean;
  points: IvSkewDailyPoint[];
  /** Sessions held back by clean_only — surfaced, never silently dropped. */
  excluded_degraded: string[];
}

export interface IvSkewIntradayRow {
  expiry: string;
  dte: number;
  rank: number;
  ok: boolean;
  confidence: IvSkewConfidence;
  quality: IvSkewQuality;
  rr?: number | null;
  fly?: number | null;
  atm_iv?: number | null;
}

export interface IvSkewIntradaySample {
  ts: string;
  session_date: string;
  underlying: OiUnderlying;
  reference: number | null;
  reference_source: string;
  expiries: IvSkewIntradayRow[];
}

export interface IvSkewSeries {
  underlying: OiUnderlying;
  session_date: string | null;
  samples: number;
  points: IvSkewIntradaySample[];
}

export interface ArbitrageConfig {
  default_exchanges: string[];
  supported_exchanges: string[];
  refresh_seconds: number;
  quote_refresh_seconds: number;
}

export interface ArbitrageLeg {
  symbol: string;
  exchange: string;
  expiry?: string;
  lotsize?: number;
  tick_size?: number | null;
}

export interface ArbitragePair {
  id: string;
  underlying: string;
  exchange: string;
  type: string;
  near: ArbitrageLeg;
  far: ArbitrageLeg;
}

export interface ArbitrageRow extends ArbitragePair {
  near_mid: number | null;
  far_mid: number | null;
  raw_spread: number | null;
  spread_pct: number | null;
  best_credit: number | null;
  direction: string | null;
  liquid: boolean;
}

export interface ArbitrageSnapshot {
  pairs: ArbitragePair[];
  symbols: Array<{ symbol: string; exchange: string }>;
  counts: { underlyings: number; pairs: number; symbols: number };
  exchanges: string[];
  generated_at: string;
  rows: ArbitrageRow[];
  updated_at: string;
}

export type OiProfileUnderlying = "NIFTY" | "BANKNIFTY" | "FINNIFTY" | "SENSEX";
export type OiProfileInterval = "1min" | "5min" | "15min";

export interface OiProfileConfig {
  underlyings: OiProfileUnderlying[];
  intervals: OiProfileInterval[];
  default_interval: OiProfileInterval;
  default_days: number;
  max_days: number;
  price_buckets: number;
  refresh_seconds: number;
}

export interface OiProfileCandle {
  t: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  oi: number;
  oi_change: number;
}

export interface OiProfileBucket {
  price_low: number;
  price_high: number;
  price_mid: number;
  buildup: number;
  unwind: number;
  net: number;
}

export interface OiProfileDailyRow {
  date: string;
  close: number;
  price_chg: number;
  price_chg_pct: number;
  volume: number;
  oi: number;
  oi_chg: number;
  oi_chg_pct: number;
  interpretation: string;
}

export interface OiProfileMeta {
  underlying: OiProfileUnderlying;
  fut_symbol: string | null;
  fut_token: number;
  exchange: string | null;
  lot_size: number | null;
  expiry: string | null;
  interval: OiProfileInterval;
  days: number;
  available_expiries: string[];
  price_buckets: number;
  strike_step?: number;
}

export interface OiProfileStats {
  current_price?: number;
  current_oi?: number;
  session_oi_change?: number;
  total_buildup?: number;
  total_unwind?: number;
  poc_price?: number | null;
  oi_walls?: number[];
  last_bar?: string;
  day_interpretation?: string;
}

export interface OiProfileSnapshot {
  ok: boolean;
  empty: boolean;
  message?: string;
  meta: OiProfileMeta;
  candles: OiProfileCandle[];
  profile: OiProfileBucket[];
  poc_price: number | null;
  daily: OiProfileDailyRow[];
  stats: OiProfileStats;
}

export interface LatencyBrokerStats {
  total_orders: number;
  failed_orders: number;
  avg_total: number;
  p50_total: number;
  p99_total: number;
  sla_150ms: number;
}

export interface LatencyStats {
  total_orders: number;
  failed_orders: number;
  success_rate: number;
  avg_rtt: number;
  avg_validation: number;
  avg_overhead: number;
  avg_total: number;
  p50_total: number;
  p90_total: number;
  p95_total: number;
  p99_total: number;
  percentile_window_days: number;
  percentile_sample: number;
  sla_100ms: number;
  sla_150ms: number;
  sla_200ms: number;
  broker_stats: Record<string, LatencyBrokerStats>;
  updated_at: string;
}

export interface LatencyRow {
  ts: string;
  order_id: string | null;
  symbol: string;
  order_type: string;
  transaction_type: string | null;
  broker: string;
  status: string;
  rtt_ms: number;
  validation_ms: number;
  overhead_ms: number;
  total_ms: number;
  error: string | null;
}

export type RollingUnderlying =
  "NIFTY" | "BANKNIFTY" | "SENSEX" | "CRUDEOIL" | "CRUDEOILM" | "NATURALGAS";
export type ReentryStyle = "zone_active" | "edge_only";
export type TradeMode = "Both" | "LongOnly" | "ShortOnly" | "ShortSignalsOnly";
export type OrderSizeMode = "lots" | "qty";

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
  size_mode: OrderSizeMode;
  size_value: number;
  execution_mode?: "auto" | "confirm";
  exit_on_bar_close_only?: boolean;
  /** Exit #1: TF close against entry (Short above / Long below). Default true. */
  entry_exit_enabled?: boolean;
}

export interface ExecutionQueueItem {
  leg_id: string;
  source: string;
  instance_id?: string | null;
  owner_label?: string | null;
  instrument: string;
  exchange: string;
  tradingsymbol: string;
  option_type?: "CE" | "PE" | null;
  strike?: number | null;
  status: string;
  side?: string | null;
  qty: number;
  managed: boolean;
  entry_price?: number | null;
  ltp?: number | null;
  pnl?: number | null;
  exit_triggers?: Record<string, unknown> | null;
  signal_note?: string | null;
  actions: string[];
  meta?: Record<string, unknown>;
}

export interface ExecutionQueueSummary {
  pending_count: number;
  active_count: number;
  orphan_count: number;
  error_count: number;
  armed: boolean;
  mode: string;
  kite_authenticated: boolean;
  rs_runner?: string | null;
  rs_underlying?: string | null;
}

export interface ExecutionQueueResponse {
  pending: ExecutionQueueItem[];
  active: ExecutionQueueItem[];
  orphans: ExecutionQueueItem[];
  errors: { leg_id: string; message: string; source?: string }[];
  summary: ExecutionQueueSummary;
  arm?: { armed?: boolean; mode?: string; note?: string };
}

export interface RollingLegState {
  status: "flat" | "open" | string;
  reentries_used: number;
  entries_today: number;
  tradingsymbol: string | null;
  exchange: string | null;
  strike: number | null;
  entry_price: number | null;
  /** Kite position average — used for exits/UI when entry_price is missing. */
  broker_average_price?: number | null;
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
  long_ready?: boolean;
  long_entry?: boolean;
  ltp?: number | null;
  entry_side?: string | null;
  position_side?: "long" | "short" | null;
  broker_qty?: number | null;
  managed_by?: "algo" | "manual" | "external" | string | null;
  exit_params?: RollingLegExitParams;
}

export interface RollingLegExitParams {
  position_side?: "long" | "short" | null;
  trade_side_label?: string | null;
  zone_exit_label?: string | null;
  zone_exit_level?: number | null;
  zone_exit_triggered?: boolean;
  zone_exit_at_ltp?: boolean;
  zone_exit_ltp_distance?: number | null;
  st1?: number | null;
  signal_close?: number | null;
  exit_levels?: {
    order?: number;
    category: string;
    price: number | null;
    triggered?: boolean;
    rule?: string;
    distance?: number;
    enabled?: boolean;
    missing?: boolean;
  }[];
  next_exit?: {
    order?: number;
    category: string;
    price: number | null;
    triggered?: boolean;
    rule?: string;
    distance?: number;
  };
  force_exit?: string | null;
  session_end?: string | null;
  force_exit_due?: boolean;
  timeframe?: string | null;
  st_method?: string | null;
  entry_exit_enabled?: boolean;
  atr_tsl_enabled?: boolean;
  in_hold_zone?: boolean;
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
  state_underlying?: string | null;
  spot_stale?: boolean;
  spot_live_only?: boolean;
  last_signal: string | null;
  last_signal_at: string | null;
  last_tick_at: string | null;
  ce: RollingLegState;
  pe: RollingLegState;
}

export interface RollingOrphanLeg {
  leg_key: "ce" | "pe";
  exchange: string;
  tradingsymbol: string;
  quantity: number;
  average_price?: number | null;
  has_3st_order?: boolean;
  managed?: boolean;
}

export interface RollingStraddleStatus {
  config: RollingStraddleConfig;
  state: RollingStraddleState;
  arm: { armed?: boolean; mode?: "paper" | "live"; note?: string };
  kite_authenticated?: boolean;
  scheduler?: { scheduler_alive?: boolean };
  broker_mismatches?: string[];
  orphans?: RollingOrphanLeg[];
  order_quantity?: number;
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

export type PremiumBookStructure =
  "bull_put" | "bear_call" | "long_call" | "long_put" | "bull_call" | "bear_put" | "long_strangle";

export type PremiumBookTradeBias = "sell_premium" | "buy_hold";
/** @deprecated use trade_bias */
export type PremiumBookSide = "sell" | "buy";

export type PremiumBookUnderlying = "NIFTY" | "BANKNIFTY" | "SENSEX" | "CRUDEOIL" | "CRUDEOILM";

export interface PremiumBookConfig {
  underlying: PremiumBookUnderlying;
  expiry: string;
  trade_bias: PremiumBookTradeBias;
  /** Legacy alias mirrored from trade_bias */
  book_side?: PremiumBookSide;
  structure: PremiumBookStructure;
  otm_offset: number;
  width_steps: number;
  timeframe: Timeframe | string;
  entry_start: string;
  session_start: string;
  session_end: string;
  force_exit: string;
  system_mode: SystemMode;
  order_type: "MARKET" | "LIMIT";
  product: "MIS" | "NRML";
  tick_interval_sec: number;
  /** Legacy: SL convert on open short legs only (hidden in UI for verticals-only book). */
  convert_sl_to_spread?: boolean;
  /** Direction-driven structure: above→bull put, below→bear call, flat→sit out */
  auto_structure?: boolean;
  auto_start_on_boot: boolean;
  size_mode: OrderSizeMode;
  size_value: number;
  st_method: StMethod;
  atr1: number;
  factor1: number;
  atr2: number;
  factor2: number;
  atr3: number;
  factor3: number;
  st1_enabled: boolean;
  st2_enabled: boolean;
  st3_enabled: boolean;
  /** Entry requires ST1 zone + ST1&ST2 same direction; exits stay ST1-only. */
  entry_require_st1_st2?: boolean;
  adx_enabled: boolean;
  adx_period: number;
  adx_threshold: number;
  sl_mode: RiskMode;
  sl_value: number;
  tgt_mode: RiskMode;
  tgt_value: number;
  tsl_mode: RiskMode | "ATR";
  tsl_value: number;
  entry_exit_enabled: boolean;
  exit_on_bar_close_only: boolean;
}

export interface PremiumBookState {
  runner: string;
  morning_bar_seen?: boolean;
  current_atm?: number | null;
  last_spot?: number | null;
  last_signal?: string | null;
  last_tick_at?: string | null;
  last_error?: string | null;
  package?: {
    status?: string;
    structure?: string | null;
    net_credit?: number | null;
    net_debit?: number | null;
    max_loss?: number | null;
    legs?: Record<string, unknown>[];
    last_action?: string | null;
  };
  ce?: RollingLegState | Record<string, unknown>;
  pe?: RollingLegState | Record<string, unknown>;
  preview?: Record<string, unknown> | null;
}

export interface PremiumBookPreview {
  template?: string;
  template_label?: string;
  spot?: number | null;
  atm?: number | null;
  net_credit?: number;
  net_debit?: number;
  is_debit?: boolean;
  max_loss_estimate?: number | null;
  legs?: {
    tradingsymbol?: string;
    side?: string;
    strike?: number;
    option_type?: string;
    ltp?: number;
  }[];
  error?: string;
}

export interface PremiumBookStatus {
  config: PremiumBookConfig;
  state: PremiumBookState & {
    active_structure?: string | null;
    auto_structure_reason?: string | null;
  };
  preview?: PremiumBookPreview | null;
  arm: { armed?: boolean; mode?: "paper" | "live"; note?: string };
  kite_authenticated?: boolean;
  narrative?: string;
  active_structure?: string | null;
  auto_structure_reason?: string | null;
  config_structure?: string | null;
}

export interface PremiumBookLogEntry {
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

export type RrgQuadrant = "leading" | "weakening" | "lagging" | "improving";

export type FpiConfluence = "aligned" | "divergence" | "watch" | "contrarian" | "neutral" | "n/a";

export interface RrgFpiRow {
  fpi_sector: string;
  net_equity_inr: number | null;
  flow: string;
  confluence: FpiConfluence;
  period: string;
  alias_of?: string | null;
}

export interface RrgConfig {
  defaults: {
    window: number;
    period: number;
    tail: number;
    lookback_days: number;
  };
  benchmarks: { id: string; label: string }[];
  sectors: { id: string; label: string; tradingsymbol: string }[];
  presets: {
    id: string;
    label: string;
    benchmark: string;
    symbols: string[];
  }[];
  fpi?: {
    default_period: string;
    periods: { id: string; label: string }[];
  };
}

export interface RrgTailPoint {
  date: string;
  rs: number;
  momentum: number;
}

export interface RrgSymbolRow {
  symbol: string;
  label: string;
  exchange: string;
  instrument_token: number;
  kind?: "equity" | "index";
  color: string;
  quadrant: RrgQuadrant;
  head: { rs: number; momentum: number; date: string };
  tail: RrgTailPoint[];
  fpi?: RrgFpiRow | null;
}

export interface RrgFpiMeta {
  ok: boolean;
  as_of?: string;
  period1_label?: string;
  period2_label?: string;
  fetched_at?: string;
  source_url?: string;
  stale?: boolean;
  period?: string;
  mapped_sectors?: number;
  error?: string;
}

export interface RrgSnapshot {
  ok: boolean;
  benchmark: {
    id: string;
    label: string;
    instrument_token: number;
    exchange: string;
    tradingsymbol: string;
  };
  as_of: string;
  params: {
    window: number;
    period: number;
    tail: number;
    base_date: string | null;
    lookback_days: number;
  };
  bounds: { x_min: number; x_max: number; y_min: number; y_max: number };
  regime?: Record<RrgQuadrant, number>;
  fpi?: RrgFpiMeta;
  symbols: RrgSymbolRow[];
  errors: { symbol: string; error: string }[];
}

export type AnalogueCycleKind = "weekly" | "monthly";

export interface AnalogueConfig {
  underlyings: OiUnderlying[];
  cycle_kinds: AnalogueCycleKind[];
  default_cycle_kind: AnalogueCycleKind;
  default_similarity_band_pct: number;
  similarity_band_min: number;
  similarity_band_max: number;
  max_lookback_days: number;
  max_analogue_paths: number;
  refresh_seconds: number;
  expiry_weekday_cutover?: string;
  expiry_weekdays?: Record<string, { before: string; on_or_after_cutover: string; note?: string }>;
  note?: string;
}

export interface AnaloguePathPoint {
  day: number;
  cum_pct: number;
  date?: string;
}

export interface AnaloguePathSample {
  expiry: string;
  start_date: string;
  ended_up: boolean;
  path: AnaloguePathPoint[];
}

export interface AnalogueStats {
  matched: number;
  median_expiry_level: number;
  median_remaining_pct: number;
  p25_expiry_level: number;
  p75_expiry_level: number;
  p25_remaining_pct: number;
  p75_remaining_pct: number;
  p10_expiry_level: number;
  p90_expiry_level: number;
  p10_remaining_pct: number;
  p90_remaining_pct: number;
  p_further_up: number;
  p_further_down: number;
  p_further_flat: number;
}

export interface AnalogueSnapshot {
  underlying: string;
  label?: string;
  cycle_kind: AnalogueCycleKind;
  cycle_start: string | null;
  cycle_pending?: boolean;
  prev_expiry: string;
  current_expiry: string;
  as_of: string;
  day_in_cycle: number;
  days_remaining: number;
  cycle_length_est: number;
  spot: number;
  cycle_start_px: number;
  move_so_far_pct: number;
  move_used_for_match_pct: number;
  override_move_pct?: number | null;
  similarity_band_pct: number;
  matched: number;
  stats: AnalogueStats | null;
  current_path: AnaloguePathPoint[];
  analogue_paths: AnaloguePathSample[];
  median_path: AnaloguePathPoint[];
  p25_path: AnaloguePathPoint[];
  p75_path: AnaloguePathPoint[];
  reasoning: string[];
  disclaimer?: string;
  engine?: string;
  bars_used?: number;
  lookback_days_requested?: number;
  updated_at?: string;
}

export interface PricingEngineConfig {
  underlyings: OiUnderlying[];
  strike_count: number;
  refresh_seconds: number;
  risk_free_rate: number;
  heston_defaults?: Record<string, number>;
  recommendations?: {
    atm_window_steps?: number;
    min_ltp?: number;
    max_ideas?: number;
  };
  note?: string;
}

export interface PricingLegQuote {
  ltp: number | null;
  iv: number | null;
  bs_fair: number | null;
  edge: number | null;
  edge_pct?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  heston_fair?: number | null;
  heston_edge?: number | null;
}

export interface PricingStrikeRow {
  strike: number;
  ce: PricingLegQuote;
  pe: PricingLegQuote;
  straddle_ltp?: number | null;
  straddle_bs?: number | null;
  straddle_edge?: number | null;
}

export interface PricingRecommendationLeg {
  side: "buy" | "sell";
  option_type: "CE" | "PE";
  strike: number;
  ltp: number;
  edge: number;
}

export interface PricingRecommendation {
  id: string;
  structure: string;
  title: string;
  bias?: string;
  action: "credit" | "debit" | string;
  legs: PricingRecommendationLeg[];
  net_premium: number;
  width: number;
  max_profit: number;
  max_loss: number;
  breakeven: number;
  lot_size: number;
  net_premium_inr: number;
  max_profit_inr: number;
  max_loss_inr: number;
  fair_net_premium?: number | null;
  edge_vs_fair?: number | null;
  score?: number;
  reasoning: string;
  disclaimer?: string;
  spot?: number;
  atm_strike?: number;
  underlying?: string;
}

export interface PricingDeskSnapshot {
  underlying: OiUnderlying | string;
  expiry: string;
  spot: number;
  atm_strike: number;
  atm_iv: number | null;
  tte_years?: number;
  risk_free_rate?: number;
  include_heston?: boolean;
  rows: PricingStrikeRow[];
  recommendations?: PricingRecommendation[];
  updated_at: string;
  engine?: string;
  note?: string;
}

export interface PricingCalcResult {
  bs: Record<string, number | string | null>;
  heston?: Record<string, number | null> | null;
}

/** IV vs GARCH-forecast realized vol desk — GET /dashboard/iv-vs-garch/{config,data} */
export type IvGarchUnderlying = "NIFTY" | "BANKNIFTY";

export interface IvGarchThresholds {
  rich_above: number;
  cheap_below: number;
}

export interface IvGarchConfig {
  underlyings: IvGarchUnderlying[];
  history_days: number;
  fit_years: number;
  refresh_seconds: number;
  thresholds: IvGarchThresholds;
  signals: { rich: string; cheap: string; neutral: string };
}

export interface IvGarchHistoryPoint {
  date: string;
  iv: number;
  garch_forecast: number;
  spread: number;
}

export interface IvGarchAtmLeg {
  tradingsymbol: string;
  last_price: number | null;
  iv: number | null;
  error?: string;
}

export interface IvGarchAtm {
  expiry: string;
  spot: number;
  atm_strike: number;
  days_to_expiry: number;
  ce: IvGarchAtmLeg | null;
  pe: IvGarchAtmLeg | null;
  atm_iv: number;
  leg_errors: string[] | null;
}

export interface IvGarchModel {
  spec: string;
  observations: number;
  sample_start: string;
  sample_end: string;
  omega: number;
  alpha: number;
  beta: number;
  persistence: number;
  long_run_vol: number | null;
  log_likelihood: number;
}

export interface IvGarchHistoryMeta {
  iv_history_source: string;
  iv_history_scale: number;
  days: number;
  mean_spread: number;
  latest_history_spread: number;
  latest_history_date: string;
  latest_spread_percentile: number | null;
}

export interface IvGarchSnapshot {
  instrument: IvGarchUnderlying;
  current_iv: number;
  garch_forecast_vol: number;
  spread: number;
  signal: string;
  history: IvGarchHistoryPoint[];
  thresholds: IvGarchThresholds;
  atm: IvGarchAtm;
  garch: IvGarchModel;
  history_meta: IvGarchHistoryMeta;
  notes: string[];
  updated_at: string;
}

/** Expiry Calendar — GET /expiry-calendar */
export type ExpiryCalInstrumentFilter = "all" | "opt" | "fut";
export type ExpiryCalKindFilter = "all" | "weekly" | "monthly";
export type ExpiryCalAssetClass = "all" | "indices" | "stocks" | "commodities";
export type ExpiryCalExchange = "NFO" | "BFO" | "MCX";

export interface ExpiryCalConfig {
  supported_exchanges: ExpiryCalExchange[];
  default_exchanges: ExpiryCalExchange[];
  segment_map: Record<string, ExpiryCalExchange>;
  instrument_filters: ExpiryCalInstrumentFilter[];
  kind_filters: ExpiryCalKindFilter[];
  asset_class_filters: ExpiryCalAssetClass[];
  index_underlyings: string[];
}

export interface ExpiryCalItem {
  exchange: ExpiryCalExchange | string;
  segment: string;
  underlying: string;
  label: string;
  instrument_family: "OPT" | "FUT" | string;
  instrument_types: string[];
  has_fut_same_day: boolean;
  expiry: string;
  expiry_kind: "weekly" | "monthly" | string;
  lot_size: number;
  contract_count: number;
  strike_min: number | null;
  strike_max: number | null;
  color_key: string;
  asset_class?: ExpiryCalAssetClass | string;
}

export interface ExpiryCalDay {
  date: string;
  expiries: ExpiryCalItem[];
}

export interface ExpiryCalUnderlying {
  underlying: string;
  exchange: string;
  label: string;
  color_key: string;
  asset_class?: ExpiryCalAssetClass | string;
  expiry_dates: string[];
}

export interface ExpiryCalMonthBoard {
  as_of: string;
  year: number;
  month: number;
  exchanges: string[];
  instrument: string;
  kind: string;
  asset_class?: string;
  q: string;
  instruments_cache_updated: string | null;
  instruments_cache_fresh: boolean;
  days: ExpiryCalDay[];
  underlyings: ExpiryCalUnderlying[];
  day_count: number;
  expiry_row_count: number;
  month_days: number;
}

/* ------------------------------------------------------------------ */
/* Equity Report desk (/equity-report)                                  */
/* ------------------------------------------------------------------ */

export interface InstrumentSearchResponse {
  q: string;
  segment: string;
  items: InstrumentHit[];
}

export interface EquityPin {
  symbol: string;
  company: string;
  exchange: string;
  pinned_at: string;
}

export type EquityReportStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled";

export interface EquityReportProgress {
  iteration?: number;
  tool_calls?: number;
  note?: string;
}

export interface EquityReportCitation {
  url: string;
  title: string;
}

export interface EquityReportUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
}

export interface EquityReportJob {
  id: string;
  ticker: string;
  company: string;
  exchange: string;
  status: EquityReportStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: EquityReportProgress;
  usage: EquityReportUsage;
  cost_usd: number;
  citations: EquityReportCitation[];
  model: string;
  error: string | null;
  /** Only present on GET /equity/reports/{id}. */
  markdown?: string;
}

export interface EquityReportListResponse {
  jobs: EquityReportJob[];
  anthropic_ready: boolean;
  stub_mode: boolean;
  daily_usd_cap: number;
  spent_today_usd: number;
  remaining_usd: number | null;
  capped: boolean;
}

export interface VelocityStatus {
  alive: boolean;
  underlyings: string[];
  strike_width: number;
  expiries_tracked: number;
  last_report: {
    ok?: boolean;
    ts?: string;
    error?: string;
    underlyings?: Record<
      string,
      { spot?: number; legs?: number; legs_valid?: number; error?: string }
    >;
  };
  state: Record<string, unknown>;
}

export interface VelocityCoverageDay {
  date: string;
  minutes: number;
  legs: number;
}

export interface VelocityCoverage {
  underlying: string;
  sessions: number;
  first: string | null;
  last: string | null;
  days: VelocityCoverageDay[];
}

export interface VelocityPoint {
  ts: string;
  expiry: string;
  strike: number;
  option_type: string;
  v_t: number;
}

export interface VelocitySeries {
  underlying: string;
  session_date: string | null;
  minutes: number;
  observations?: number;
  blanks?: string;
  points: VelocityPoint[];
}

export interface VelocityChartMinute {
  ts: string;
  clock: string;
  spot: number;
  v_max: number | null;
  v_med: number | null;
  v_atm_ce: number | null;
  v_atm_pe: number | null;
}

export interface VelocityLagPoint {
  lag_min: number;
  corr: number | null;
  n: number;
}

export interface VelocityLadderPoint {
  clock: string;
  change: number;
}

export interface VelocityLadderSeries {
  label: string;
  strike: number;
  option_type: string;
  offset: number;
  baseline_ts: string;
  baseline_ltp: number;
  /** Rebased to the session open (vs a later first appearance). Not coverage. */
  baseline_at_open: boolean;
  /** Minutes actually present; a strike can leave the tracked window mid-session. */
  coverage: number;
  points: VelocityLadderPoint[];
}

export interface VelocityLadder {
  baseline_ts: string | null;
  atm_at_open: number | null;
  step: number;
  series: VelocityLadderSeries[];
}

export interface VelocityContext {
  spot: number | null;
  spot_change: number | null;
  spot_change_pct: number | null;
  atm: number | null;
  straddle: number | null;
  straddle_pct: number | null;
  pcr: number | null;
  ce_oi: number | null;
  pe_oi: number | null;
  /** PCR/OI are over the tracked window, not the full chain. Always show this. */
  scope: string | null;
}

export interface VelocityChart {
  underlying: string;
  session_date: string | null;
  atm_strike: number | null;
  nearest_expiry?: string;
  /** Every expiry archived this session, for the selector. */
  expiries: string[];
  /** null = pooled across expiries. */
  selected_expiry: string | null;
  contracts: number;
  ladder: VelocityLadder;
  context: VelocityContext;
  minutes: VelocityChartMinute[];
  thresholds: { p95?: number | null; p99?: number | null };
  correlation: {
    n: number;
    lag_profile: VelocityLagPoint[];
    best_lag: number | null;
    best_corr?: number | null;
    contemporaneous: number | null;
    interpretation: string;
  };
}

/* ---------------------------------------------------------------------------
 * Theta Decay desk (/decay/*)
 *
 * Reads the same archive as Delta Velocity — hence the reuse of
 * VelocityContext and VelocityLagPoint rather than near-identical copies.
 * ------------------------------------------------------------------------ */

export interface ThetaDecayMinute {
  ts: string;
  clock: string;
  spot: number;
  /** ATM straddle burn, re-struck each minute against that minute's spot. */
  burn_straddle: number | null;
  /** Fixed at the session's closing ATM strike — a different question. */
  burn_atm_ce: number | null;
  burn_atm_pe: number | null;
  burn_med: number | null;
}

export interface ThetaBurnBucket {
  dte: number;
  n: number;
  p50: number;
  p95: number;
  expiry: string;
}

export interface ThetaBurnStrike {
  strike: number;
  option_type: string;
  premium: number | null;
  theta: number | null;
  burn_pct_day: number | null;
}

/** Why a capture ratio is or is not worth reading. See features.capture_quality. */
export type ThetaCaptureQuality =
  | "ok"
  | "too_few_windows"
  | "theta_too_small"
  | "vega_dominated"
  | "no_data";

export interface ThetaCaptureBucket {
  dte: number;
  /** Rows: one per (contract, window). Not a sample count — see time_windows. */
  windows: number;
  /** Distinct clock windows. The strikes inside one window are not independent. */
  time_windows: number;
  capture: number | null;
  quality: ThetaCaptureQuality;
  /** Share of absolute price movement the theta term explains — the honesty number. */
  theta_share: number | null;
  vega_share: number | null;
  theoretical: number;
  realized: number;
}

export interface ThetaDecayChart {
  underlying: string;
  session_date: string | null;
  atm_strike: number | null;
  expiries: string[];
  selected_expiry: string | null;
  contracts: number;
  step: number;
  context: VelocityContext;
  minutes: ThetaDecayMinute[];
  burn_by_dte: ThetaBurnBucket[];
  burn_by_strike: ThetaBurnStrike[];
  capture: {
    horizon_min: number;
    by_dte: ThetaCaptureBucket[];
    /** Always populated — explains an empty or partly-gated table. */
    note: string;
  };
}

export interface ThetaVelocityMinute {
  ts: string;
  clock: string;
  spot: number;
  tau_med: number | null;
  tau_max: number | null;
}

export interface ThetaVelocityChart {
  underlying: string;
  session_date: string | null;
  selected_expiry: string | null;
  minutes: ThetaVelocityMinute[];
  thresholds: { p95?: number | null; p99?: number | null };
  blanks: string;
  correlation: {
    n: number;
    lag_profile: VelocityLagPoint[];
    best_lag: number | null;
    best_corr?: number | null;
    contemporaneous: number | null;
    interpretation: string;
  };
}

export interface ThetaDecayStatus {
  source: string;
  collector_alive: boolean;
  underlyings: string[];
  coverage: VelocityCoverage;
  defaults: {
    horizon_min: number;
    min_premium: number;
    min_theta_share: number;
    max_vega_share: number;
    risk_free_rate: number;
    dividend_yield: number;
  };
}

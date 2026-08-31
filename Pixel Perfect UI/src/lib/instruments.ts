import type { RollingUnderlying } from "@/lib/types";

/** Contract lot sizes, mirroring `INDEX_OPTIONS` in the backend's `config.py`.
 *
 *  Duplicated from the backend because no endpoint exposes it. Both the
 *  rolling-straddle and survivor desks check the configured quantity is a
 *  multiple of the lot before letting an order be placed, and the backend
 *  re-checks with `survivor_store.lot_size_for()`. Keep the two in step: a
 *  wrong value here shows the operator a quantity the UI calls valid and the
 *  backend then rejects.
 *
 *  Exhaustive over `RollingUnderlying` deliberately. Adding a name to that
 *  union should fail to compile here until someone supplies its lot size —
 *  which is precisely what did not happen when the MCX three were added, and
 *  left the survivor desk's private copy of this table three entries short.
 */
export const LOT_SIZES: Record<RollingUnderlying, number> = {
  NIFTY: 65,
  BANKNIFTY: 30,
  SENSEX: 20,
  CRUDEOIL: 1,
  CRUDEOILM: 1,
  NATURALGAS: 1,
};

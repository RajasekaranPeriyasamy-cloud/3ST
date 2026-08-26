# Volume Footprint — where everything lives

Every file belonging to this desk, and for the code, why it sits where it does.

## Documentation and reference — all here

| File | What it is |
| --- | --- |
| [README.md](README.md) | How the desk works; the three caveats; cost table |
| [DECISIONS.md](DECISIONS.md) | All 11 decisions with reasoning, rejected alternatives, open items |
| `reference/The Reconstructed Footprint.pdf` | Upstream write-up of the method (592 KB) |
| `reference/presentation.html` | Upstream presentation (84 KB) |
| `vendor/volume_footprint/README.md` | The port's own documentation, verbatim from upstream |
| `vendor/volume_footprint/pine-source-original.txt` | Original Pine v6 source (116 KB), kept as provenance |
| `docs/CONVERSATION_SUMMARY.md` → `2026-08-20 (later)` | What shipped, in the chronological dev log |

## Code — and why it cannot be consolidated here

These are **not** movable into this folder. Each sits where a framework or
convention requires it; moving them breaks the desk:

| File | Why it must stay |
| --- | --- |
| `vendor/volume_footprint/*.py` | Third-party MPL-2.0 package (decision 5). Importable as `vendor.volume_footprint`; a path with spaces or a docs subtree is not a Python package root |
| `analysis/volume_profile/service.py` | First-party adapter. `analysis/` is the repo's convention for desk engines, and it must import cleanly under uvicorn, pytest and the schedulers |
| `api/main.py` → `/volume-footprint/{config,snapshot}` | 3ST has one FastAPI app; routes cannot live outside it |
| `Pixel Perfect UI/src/routes/volume-footprint.tsx` | TanStack Router is **file-based** — the route only exists because the file is in `src/routes/`. Moving it deletes the page |
| `Pixel Perfect UI/src/components/gamma/concentration/VolumeConfluencePanel.tsx` | Imported by `ConcentrationBoard.tsx`; belongs with the tab it renders on |
| `Pixel Perfect UI/src/components/gamma/concentration/GammaLadder.tsx` | Row tint lives inside the existing ladder — it is a modification, not a new file |
| `Pixel Perfect UI/src/components/AppSidebar.tsx` | The pinned sidebar entry |
| `tests/test_volume_footprint.py` | 37 upstream engine tests. In `tests/` so `pytest tests/` and CI run them (decision 6) |
| `tests/test_volume_profile.py` | 12 adapter tests — basis alignment, thin-session guard, exact strike bands, peek-only cache contract |
| `options/session_poc.py` | Prefers the footprint POC via peek (decision 11); it is a pre-existing module this desk modifies |
| `instruments.py` | `_compact_row` now carries `tick_size`, which the price lattice reads |
| `options/gamma_density.py` | Attaches `volume_profile` + `strike_volume` to the snapshot, gated on the full desk poll |

**So the split is deliberate:** everything that *describes* the desk is
consolidated here; everything the running application loads stays on the path its
framework demands. This file is the index tying the two together.

## Verify it still hangs together

```bash
pytest tests/test_volume_footprint.py tests/test_volume_profile.py -q
```

```bash
curl -s "http://127.0.0.1:8001/volume-footprint/snapshot?underlying=NIFTY"
```

## History of this folder

`volume Profile Gaucessian/` started as the original untracked working folder. On
2026-08-20 its package was vendored byte-identical, its tests moved into `tests/`
(import path rewritten), its Pine source kept as
`vendor/volume_footprint/pine-source-original.txt`, and its PDF and presentation
moved into the docs tree — each file diffed against its destination first, so
nothing unique was lost. The emptied folder was left behind.

On 2026-08-26 it was re-adopted as this desk's documentation home: `docs/volume-footprint/`
was moved here wholesale and removed. The code stayed put for the reasons in the
table above — in particular, a directory name containing spaces cannot be a Python
package root, so `vendor/volume_footprint/` can never live here.

"""Third-party code vendored into 3ST, kept separate from first-party modules.

Each subpackage carries its own upstream LICENSE and keeps its original layout so
it stays diffable against the source it came from. Import them explicitly
(``from vendor.volume_footprint import ...``) rather than via sys.path juggling —
the same import then resolves identically under pytest, uvicorn and the schedulers.
"""

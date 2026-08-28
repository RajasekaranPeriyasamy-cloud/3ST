"""Atomic JSON store writes — the fix for the 2026-08-27 history corruption.

``data/gamma_density_history.json`` was left as a valid JSON document followed
by 348 bytes of a longer payload's tail, after two processes shared the fixed
``<name>.tmp`` scratch path. Every read then fell back to an empty store and
the Gamma Density chart showed "Waiting for in-session GEX samples" all session.

The invariant these tests pin: a writer's scratch path is private to that
writer, so concurrent writes can lose an update but can never corrupt the file.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from utils.atomic_json import atomic_write_json, temp_path_for

# --- the scratch path is private to each writer ---------------------------


def test_temp_paths_are_unique_per_call(tmp_path):
    """The whole bug in one assertion: two writers must not share a temp path."""
    target = tmp_path / "store.json"
    seen = {temp_path_for(target) for _ in range(200)}
    assert len(seen) == 200


def test_temp_path_sits_beside_the_target_and_names_the_process(tmp_path):
    target = tmp_path / "store.json"
    tmp = temp_path_for(target)
    assert tmp.parent == target.parent, "os.replace must stay on one filesystem"
    assert tmp.name.startswith("store.json.")
    assert str(os.getpid()) in tmp.name
    assert tmp.suffix == ".tmp"


# --- basic write behaviour -------------------------------------------------


def test_writes_parseable_json_and_creates_parent(tmp_path):
    target = tmp_path / "nested" / "store.json"
    atomic_write_json(target, {"series": {"NIFTY": [1, 2, 3]}}, indent=2)
    assert json.loads(target.read_text(encoding="utf-8")) == {"series": {"NIFTY": [1, 2, 3]}}


def test_forwards_dumps_kwargs(tmp_path):
    target = tmp_path / "store.json"
    atomic_write_json(target, {"b": 2, "a": 1}, indent=2, sort_keys=True)
    body = target.read_text(encoding="utf-8")
    assert body.index('"a"') < body.index('"b"')


def test_default_hook_is_forwarded(tmp_path):
    from datetime import date

    target = tmp_path / "store.json"
    atomic_write_json(target, {"d": date(2026, 8, 27)}, indent=2, default=str)
    assert json.loads(target.read_text(encoding="utf-8")) == {"d": "2026-08-27"}


def test_replaces_an_existing_file(tmp_path):
    target = tmp_path / "store.json"
    atomic_write_json(target, {"v": 1})
    atomic_write_json(target, {"v": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}


def test_leaves_no_scratch_files_behind(tmp_path):
    target = tmp_path / "store.json"
    for i in range(5):
        atomic_write_json(target, {"v": i})
    assert list(tmp_path.glob("*.tmp")) == []


# --- failure cleanup -------------------------------------------------------


def test_failed_serialisation_cleans_up_and_leaves_the_store_intact(tmp_path):
    target = tmp_path / "store.json"
    atomic_write_json(target, {"v": "original"})

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": Unserialisable()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"v": "original"}
    assert list(tmp_path.glob("*.tmp")) == [], "a failed write must not leak scratch files"


# --- the regression ---------------------------------------------------------


def test_concurrent_writers_never_corrupt_the_store(tmp_path):
    """Reproduces the 2026-08-27 shape: writers with very different payload sizes.

    With a shared temp path, the short writer replaces the store while the long
    writer is still emitting, and the long writer's tail lands past the end of
    the short document. Here every write must leave a file that parses, and the
    content must be one writer's payload -- never a blend of two.
    """
    target = tmp_path / "history.json"
    big = {"series": {f"KEY{i}": list(range(300)) for i in range(40)}}
    small = {"series": {"KEY0": [1]}}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def writer(payload, n):
        try:
            barrier.wait(timeout=10)
            for _ in range(n):
                atomic_write_json(target, payload, indent=2)
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(big, 40)),
        threading.Thread(target=writer, args=(small, 40)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"writers raised: {errors}"
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded in (big, small), "store must hold exactly one writer's payload"
    assert list(tmp_path.glob("*.tmp")) == []


def test_reader_never_observes_a_partial_document(tmp_path):
    """A reader racing writers must always parse, never see a truncated file."""
    target = tmp_path / "history.json"
    atomic_write_json(target, {"series": {}}, indent=2)
    big = {"series": {f"KEY{i}": list(range(300)) for i in range(40)}}
    stop = threading.Event()
    failures: list[str] = []

    def writer():
        while not stop.is_set():
            atomic_write_json(target, big, indent=2)

    def reader():
        for _ in range(300):
            try:
                json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                failures.append(str(exc))
                return
            except (OSError, FileNotFoundError):
                # A replace in flight is fine; a *parse* failure is not.
                continue

    w = threading.Thread(target=writer, daemon=True)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    r.join(timeout=30)
    stop.set()
    w.join(timeout=10)

    assert not failures, f"reader saw a corrupt document: {failures[:1]}"


# --- the stores actually use it --------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "options/gamma_density_history.py",
        "analysis/delta_velocity/store.py",
        "analysis/volume_profile/tilt_history.py",
    ],
)
def test_no_store_reintroduces_a_fixed_temp_name(module_path):
    """A new store copying the old pattern re-opens the corruption window."""
    src = (Path(__file__).resolve().parents[1] / module_path).read_text(encoding="utf-8")
    assert '.tmp"' not in src, (
        f"{module_path} builds a fixed temp filename again - "
        "use utils.atomic_json.atomic_write_json instead"
    )
    assert "atomic_write_json" in src

"""Canvas state: the backend's truth about what is on screen.

The board is an evolving artifact, not a feed. That distinction lives here.

Every visual belongs to a topic `key`. Draw about a key that already exists and
the existing block GROWS (`block.update`); contradict one and we BRANCH beside it
(`block.add` + `link.add`) so nothing is silently overwritten.

`key` and `revision` are backend-only. Nothing here changes the wire protocol —
we still emit exactly the ops in CONTRACT.md. Do not leak keys into `data`.
"""
import difflib
import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import ops

# --- tuning knobs -----------------------------------------------------------
MAX_BULLETS = 6         # a card may grow past the per-call cap of 4, but not forever
MAX_BRANCHES = 2        # a subject may hold a disagreement, not an argument
KEY_CUTOFF = 0.8        # difflib similarity for collapsing near-duplicate keys
COOLDOWN_S = 6.0        # per-key debounce; one long sentence must not update thrice
# How long a topic keeps the shape it was first given. Measured over one real
# session: the `sentry` key cycled hero -> photo -> concept -> hero -> photo, and
# the room watched one card flip between an emoji, a photograph and a bullet list.
# Eight of 51 writes were these type-change replaces. Growing a card is alive;
# swapping its form under the audience is thrash.
FORM_LOCK_S = float(__import__("os").getenv("FORM_LOCK", "12.0"))
FOCUS_THROTTLE_S = 3.5  # camera moves per second budget, or the room gets seasick

# What the stage can hold. The stage retires a scene after LIFETIME with no refresh,
# and retires the oldest once MAX_LIVE are up. If we do not mirror that, the manifest
# tells the model that things are on screen which the audience can no longer see —
# and the whole point of the manifest is that it is the truth.
#
# These were once hand-copied constants with a comment asking the next person to keep
# them in step with stage.js. They drifted: this said 3 while the stage showed 1, so
# the model spent its calls revising two cards nobody could see. The stage now
# announces its own policy on connect (`cmd: "hello"`) and owns the number; these are
# only the defaults for a client too old to say.
STAGE_LIFETIME_S = 26.0
STAGE_MAX_LIVE = 1


def set_stage_policy(max_live: int | None, lifetime_ms: float | None) -> dict:
    """Adopt the policy the display reports. One owner for the number, so it cannot
    drift out of step again."""
    global STAGE_MAX_LIVE, STAGE_LIFETIME_S
    if max_live is not None:
        STAGE_MAX_LIVE = max(1, min(12, int(max_live)))
    if lifetime_ms is not None:
        STAGE_LIFETIME_S = max(2.0, min(600.0, float(lifetime_ms) / 1000.0))
    return {"max_live": STAGE_MAX_LIVE, "lifetime_s": STAGE_LIFETIME_S}

_COL_PITCH = 1200       # wide enough that a branch never collides with the next column
_COL_BASE = -220        # centres a standard 440-wide block on the origin
_ROW_GAP = 40
_BRANCH_GAP = 60
_Y0 = -180


@dataclass
class Block:
    id: str
    type: str
    key: str
    data: dict
    x: int
    y: int
    w: int
    h: int
    created: float
    touched: float
    revision: int = 0
    parent: str | None = None      # key of the block this one contradicts

    @property
    def cluster(self) -> str:
        return self.key.split(".")[0]


# --- state ------------------------------------------------------------------
BLOCKS: dict[str, Block] = {}
BY_KEY: dict[str, str] = {}
LINKS: dict[str, dict] = {}

_cluster_col: dict[str, int] = {}
_cluster_bottom: dict[str, int] = {}
_alt_n = itertools.count(1)
_last_focus = 0.0


def reset() -> None:
    """Wipe backend state. Block ids keep counting up; `seq` must stay monotonic."""
    global _last_focus
    BLOCKS.clear()
    BY_KEY.clear()
    LINKS.clear()
    _cluster_col.clear()
    _cluster_bottom.clear()
    _last_focus = 0.0


def _forget(bid: str) -> None:
    """Drop local state for a block the stage has already retired. Emits nothing —
    the frontend removed it on its own."""
    b = BLOCKS.pop(bid, None)
    if b is None:
        return
    BY_KEY.pop(b.key, None)
    for lid in [i for i, l in LINKS.items() if bid in (l["from"], l["to"])]:
        LINKS.pop(lid, None)
    if not any(x.cluster == b.cluster for x in BLOCKS.values()):
        _cluster_bottom.pop(b.cluster, None)


def prune() -> None:
    """Forget what the stage has retired, so the manifest never lies."""
    now = time.time()
    keep = [b.id for b in sorted(BLOCKS.values(), key=lambda x: -x.touched)
            if now - b.touched <= STAGE_LIFETIME_S][:STAGE_MAX_LIVE]
    for bid in [i for i in list(BLOCKS) if i not in keep]:
        _forget(bid)


# --- keys -------------------------------------------------------------------
def key_of_block(ref: str | None) -> str | None:
    """Map a block id back to its key. The model is told to pass a key but the
    manifest shows ids too, and it passes `b_2` often enough to matter."""
    b = BLOCKS.get((ref or "").strip())
    return b.key if b else None


def normalize_key(raw: str | None) -> str:
    """Slugify, then collapse onto an existing near-identical key.

    The model will say 'pricing', 'price' and 'pricing model' for one topic. Every
    miss here is a duplicate card on screen, which is the exact bug we are fixing.
    """
    slug = re.sub(r"[^a-z0-9.]+", "-", (raw or "").strip().lower()).strip("-.")
    if not slug:
        return "misc"
    # `.altN` suffixes are OURS: they are how a contradiction gets filed. The model
    # sees them in the manifest and starts writing to them, which produced chains
    # like construction.alt2 -> construction.alt4 and a branch on every single line.
    # Strip it so a caller can only ever name a top-level subject.
    slug = re.sub(r"\.alt\d+$", "", slug) or "misc"
    if slug in BY_KEY:
        return slug

    # Only ever collapse onto a top-level topic, never onto a branch key.
    # Longest first, so the most specific existing topic wins.
    topics = sorted((k for k in BY_KEY if "." not in k), key=len, reverse=True)

    # A slug that extends an existing topic IS that topic, said more specifically:
    # 'pricing-model' -> 'pricing'. difflib scores that pair 0.70 and would miss it,
    # and every miss is a duplicate card on screen.
    for c in topics:
        if slug.startswith(f"{c}-") or c.startswith(f"{slug}-"):
            return c

    # Fallback catches inflections difflib is good at: 'price' -> 'pricing'.
    match = difflib.get_close_matches(slug, topics, n=1, cutoff=KEY_CUTOFF)
    return match[0] if match else slug


# --- merge rules ------------------------------------------------------------
# This is where "the card grows smoothly" is won or lost.

def _clean(d: dict) -> dict:
    return {k: v for k, v in (d or {}).items() if v is not None}


def _merge_replace(old: dict, new: dict) -> dict:
    return {**old, **_clean(new)}


def _merge_text(old: dict, new: dict) -> dict:
    out = {**old, **_clean({k: new.get(k) for k in ("title", "body", "accent")})}
    bullets = list(old.get("bullets") or [])
    seen = {b.strip().lower() for b in bullets}
    for b in new.get("bullets") or []:
        k = b.strip().lower()
        if k and k not in seen:
            bullets.append(b)
            seen.add(k)
    out["bullets"] = bullets[-MAX_BULLETS:]
    return out


def _merge_stat(old: dict, new: dict) -> dict:
    return {**old, **_clean({k: new.get(k)
                             for k in ("value", "label", "delta", "unit")})}


def _merge_chart(old: dict, new: dict) -> dict:
    """Merge series BY LABEL — a chart that fills in as numbers get spoken."""
    out = {**old, **_clean({k: new.get(k) for k in ("title", "kind", "unit")})}
    series = [dict(s) for s in (old.get("series") or [])]
    idx = {str(s.get("label", "")).strip().lower(): i for i, s in enumerate(series)}
    for s in new.get("series") or []:
        k = str(s.get("label", "")).strip().lower()
        if k in idx:
            series[idx[k]] = dict(s)
        else:
            idx[k] = len(series)
            series.append(dict(s))
    out["series"] = series
    return out


def _merge_table(old: dict, new: dict) -> dict:
    """Merge rows by first cell; widen columns but never narrow them."""
    out = {**old, **_clean({"title": new.get("title")})}
    old_cols, new_cols = old.get("columns") or [], new.get("columns") or []
    out["columns"] = new_cols if len(new_cols) > len(old_cols) else old_cols
    rows = [list(r) for r in (old.get("rows") or [])]

    def rk(r):
        return str(r[0]).strip().lower() if r else ""

    idx = {rk(r): i for i, r in enumerate(rows)}
    for r in new.get("rows") or []:
        k = rk(r)
        if k in idx:
            rows[idx[k]] = list(r)
        else:
            idx[k] = len(rows)
            rows.append(list(r))
    out["rows"] = rows
    return out


MERGERS: dict[str, Callable[[dict, dict], dict]] = {
    "text": _merge_text,
    "stat": _merge_stat,
    "chart": _merge_chart,
    "table": _merge_table,
    # diagram/map/image/code: replaced wholesale. Mermaid source cannot be
    # merged safely, and the rest are single-payload blocks.
}


# --- layout: cluster-aware, so related things share a column ----------------
def _column_for(cluster: str) -> int:
    """Assign a column per topic, growing outward from the centre."""
    if cluster not in _cluster_col:
        i = len(_cluster_col)
        k, sign = (i + 1) // 2, (1 if i % 2 else -1)
        _cluster_col[cluster] = _COL_BASE + (0 if i == 0 else sign * k * _COL_PITCH)
    return _cluster_col[cluster]


def _place(cluster: str, h: int) -> tuple[int, int]:
    """Next free slot in a cluster's column, stacked by real height."""
    x = _column_for(cluster)
    bottom = _cluster_bottom.get(cluster)
    y = _Y0 if bottom is None else bottom + _ROW_GAP
    _cluster_bottom[cluster] = y + h
    return x, y


# --- the two write paths ----------------------------------------------------
def upsert(type_: str, key: str, data: dict, *, w: int = 440, h: int = 280,
           enter: str = "pop") -> tuple[list[dict], str, str]:
    """Add a block for a new topic, or grow the one that already owns this key.

    Returns (frames, block_id, action) where action is "add" or "update".
    """
    prune()
    existing_id = BY_KEY.get(key)
    now = time.time()

    if existing_id and BLOCKS[existing_id].type == type_:
        b = BLOCKS[existing_id]
        merger = MERGERS.get(type_, _merge_replace)
        b.data = merger(b.data, data)
        b.revision += 1
        b.touched = now
        return [ops.block_update(b.id, b.data)], b.id, "update"

    if existing_id:
        # Same subject, different shape — the speaker moved from a claim to a
        # number, so the number is now the better way to show it. REPLACE rather
        # than branch: the stage shows one thing at a time, and branching here
        # produced a new block on every type change, which was most of them.
        old = BLOCKS.pop(existing_id)
        BY_KEY.pop(old.key, None)
        x, y = old.x, old.y
        frame = ops.block_add(type_, data, x=x, y=y, w=w, h=h, enter=enter)
        bid = frame["payload"]["id"]
        BLOCKS[bid] = Block(id=bid, type=type_, key=key, data=data, x=x, y=y,
                            w=w, h=h, created=now, touched=now, parent=old.parent)
        BY_KEY[key] = bid
        return [ops.block_remove(existing_id), frame], bid, "replace"

    x, y = _place(key.split(".")[0], h)
    frame = ops.block_add(type_, data, x=x, y=y, w=w, h=h, enter=enter)
    bid = frame["payload"]["id"]
    BLOCKS[bid] = Block(id=bid, type=type_, key=key, data=data, x=x, y=y, w=w,
                        h=h, created=now, touched=now)
    BY_KEY[key] = bid
    return [frame], bid, "add"


def branch(type_: str, key: str, data: dict, *, revises: str, label: str | None = None,
           w: int = 440, h: int = 280, enter: str = "pop") -> tuple[list[dict], str]:
    """A contradiction: place beside the original with an arrow. Both survive."""
    parent_id = BY_KEY.get(normalize_key(revises))
    if parent_id is None:
        frames, bid, _ = upsert(type_, key, data, w=w, h=h, enter=enter)
        return frames, bid

    parent = BLOCKS[parent_id]
    # Two views of one subject is tension worth showing; five is noise. Past the cap
    # we grow the newest branch instead of adding another.
    siblings = [b for b in BLOCKS.values() if b.parent == parent.key]
    if len(siblings) >= MAX_BRANCHES:
        newest = max(siblings, key=lambda b: b.touched)
        merger = MERGERS.get(type_, _merge_replace)
        newest.data = merger(newest.data, data)
        newest.revision += 1
        newest.touched = time.time()
        return [ops.block_update(newest.id, newest.data)], newest.id
    alt_key = f"{parent.cluster}.alt{next(_alt_n)}"
    x, y = parent.x + parent.w + _BRANCH_GAP, parent.y
    now = time.time()

    add = ops.block_add(type_, data, x=x, y=y, w=w, h=h, enter=enter)
    bid = add["payload"]["id"]
    BLOCKS[bid] = Block(id=bid, type=type_, key=alt_key, data=data, x=x, y=y, w=w,
                        h=h, created=now, touched=now, parent=parent.key)
    BY_KEY[alt_key] = bid

    link = ops.link_add(parent_id, bid, label or "vs")
    LINKS[link["payload"]["id"]] = link["payload"]
    return [add, link], bid


def link(from_id: str, to_id: str, label: str | None = None) -> list[dict]:
    """Explicit connection between two live blocks. Silently drops dead ids."""
    if from_id not in BLOCKS or to_id not in BLOCKS or from_id == to_id:
        return []
    f = ops.link_add(from_id, to_id, label)
    LINKS[f["payload"]["id"]] = f["payload"]
    return [f]


def undo_last() -> list[dict]:
    """Remove the most recently touched block. Backs the presenter's `u` key."""
    if not BLOCKS:
        return []
    bid = max(BLOCKS, key=lambda i: BLOCKS[i].touched)
    b = BLOCKS.pop(bid)
    BY_KEY.pop(b.key, None)
    for lid in [i for i, l in LINKS.items() if bid in (l["from"], l["to"])]:
        LINKS.pop(lid)
    if not any(x.cluster == b.cluster for x in BLOCKS.values()):
        _cluster_bottom.pop(b.cluster, None)
    return [ops.block_remove(bid)]


# --- reads ------------------------------------------------------------------
def cooldown_ok(key: str, seconds: float | None = None) -> bool:
    """New topics always pass; only re-touching a live block is rate limited.

    Reads COOLDOWN_S at call time, not import time, so tests can retune it.
    """
    prune()
    seconds = COOLDOWN_S if seconds is None else seconds
    bid = BY_KEY.get(key)
    return True if bid is None else (time.time() - BLOCKS[bid].touched) >= seconds


def _title_for(b: Block) -> str:
    d = b.data
    if b.type == "stat":
        return f"{d.get('value', '?')} — {d.get('label', '')}".strip(" —")
    if b.type == "map":
        return f"{d.get('from', '?')} → {d.get('to', '?')}"
    if b.type == "image":
        return str(d.get("caption") or d.get("alt") or "image")[:60]
    return str(d.get("title") or "").strip()[:60] or f"({b.type})"


def manifest(limit: int = 12) -> list[dict[str, Any]]:
    """What the model is told is on screen. Newest activity first."""
    prune()
    now = time.time()
    out = []
    for b in sorted(BLOCKS.values(), key=lambda x: -x.touched)[:limit]:
        # Report a branch under its PARENT subject. Exposing the internal `.altN`
        # key made the model write straight to it, so every later line landed on the
        # first subject it ever picked and the whole board collapsed onto one topic.
        entry = {"id": b.id, "key": b.parent or b.key, "type": b.type,
                 "title": _title_for(b),
                 "age_s": round(now - b.touched, 1), "revisions": b.revision}
        if b.parent:
            entry["is_contrast_of"] = b.parent
        out.append(entry)
    return out


def manifest_text() -> str:
    """Flattened manifest for send_client_content (the heartbeat / resume path)."""
    if not BLOCKS:
        return "CANVAS: empty. Nothing is on screen yet."
    lines = ["CANVAS — what is on screen right now:"]
    for e in manifest():
        bits = f"  {e['id']}  key={e['key']:<18} {e['type']:<8} \"{e['title']}\""
        if e["revisions"]:
            bits += f"  (grown {e['revisions']}x)"
        if e.get("is_contrast_of"):
            bits += "  [a contrasting view of this subject]"
        lines.append(f"{bits}  {e['age_s']}s ago")
    lines.append("Reuse a key above ONLY if the new line is about that same subject. "
                 "A new subject needs a NEW key of your own choosing.")
    return "\n".join(lines)


def focus_frames(action: str, block_id: str) -> list[dict]:
    """Camera hint after a write. Throttled — an add fits all, an update zooms in."""
    global _last_focus
    now = time.time()
    if now - _last_focus < FOCUS_THROTTLE_S:
        return []
    _last_focus = now
    b = BLOCKS.get(block_id)
    if action == "add" or b is None:
        return [ops.canvas_focus([])]          # show the board growing
    ids = [x.id for x in BLOCKS.values() if x.cluster == b.cluster]
    return [ops.canvas_focus(ids, padding=100)]  # draw the eye to what changed

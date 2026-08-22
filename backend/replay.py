"""Offline harness: prove the board EVOLVES, with no mic, no API key, no network.

Feeds scripted tool calls straight through tools.dispatch and checks the frames that
come out. This is the fast loop for tuning canvas.py — a talk takes 90 seconds to
rehearse, this takes one.

    python backend/replay.py            # run the scenario, assert, exit non-zero on fail
    python backend/replay.py --quiet    # just the verdict
"""
import argparse
import pathlib
import sys
import time

import canvas
import tools

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
_fails: list[str] = []


def call(tool: str, **args) -> tuple[list[dict], dict]:
    frames, result = tools.dispatch(tool, args)
    return frames, result


def show(label: str, frames: list[dict], result: dict, quiet: bool) -> None:
    if quiet:
        return
    print(f"\n\033[1m{label}\033[0m")
    for f in frames:
        p = f["payload"]
        if f["op"] == "block.add":
            print(f"   → block.add     {p['id']} {p['type']:<8} @({p['x']},{p['y']}) "
                  f"{str(p['data'])[:64]}")
        elif f["op"] == "block.update":
            print(f"   → block.update  {p['id']}          {str(p.get('data'))[:64]}")
        elif f["op"] == "link.add":
            print(f"   → link.add      {p['from']} --\"{p['label']}\"--> {p['to']}")
        else:
            print(f"   → {f['op']:<14} {str(p)[:64]}")
    trimmed = {k: v for k, v in result.items() if k != "canvas"}
    print(f"   ↩ {trimmed}")


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{PASS if cond else FAIL} {label}" + (f"  — {detail}" if not cond else ""))
    if not cond:
        _fails.append(label)


def ops_of(frames: list[dict]) -> list[str]:
    return [f["op"] for f in frames]


def scenario(quiet: bool) -> None:
    canvas.reset()
    canvas.COOLDOWN_S = 0.25          # keep the run fast; real default is 6s
    canvas.FOCUS_THROTTLE_S = 0.0     # assert on writes, not on camera throttling

    print("\n\033[1m── 1. a new topic lands once ─────────────────────────\033[0m")
    f1, r1 = call("show_stat", key="pricing", value="40k", label="monthly burn")
    show('speaker: "we\'re at forty k a month"', f1, r1, quiet)
    check("new key emits exactly one block.add", ops_of(f1)[:1] == ["block.add"],
          str(ops_of(f1)))
    first_id = r1["block_id"]

    print("\n\033[1m── 2. more detail GROWS it, never duplicates ─────────\033[0m")
    time.sleep(0.3)
    f2, r2 = call("show_stat", key="pricing", value="720k", label="18-month total",
                  delta="40k x 18")
    show('speaker: "...so over eighteen months that\'s 720k"', f2, r2, quiet)
    check("same key emits block.update, not block.add",
          ops_of(f2)[:1] == ["block.update"], str(ops_of(f2)))
    check("it updated the SAME block id", r2["block_id"] == first_id,
          f"{r2['block_id']} != {first_id}")
    check("revision counter advanced", canvas.BLOCKS[first_id].revision == 1,
          str(canvas.BLOCKS[first_id].revision))
    check("still exactly one block on the board", len(canvas.BLOCKS) == 1,
          f"{len(canvas.BLOCKS)} blocks")

    print("\n\033[1m── 3. a near-miss key collapses onto the same block ──\033[0m")
    time.sleep(0.3)
    f3, r3 = call("show_stat", key="pricing-model", value="$720k", label="18-month total")
    show('model says key="pricing-model" for the same topic', f3, r3, quiet)
    check("fuzzy key normalised onto 'pricing'", r3["key"] == "pricing", r3["key"])
    check("no duplicate card created", len(canvas.BLOCKS) == 1, f"{len(canvas.BLOCKS)}")

    print("\n\033[1m── 4. a contradiction BRANCHES, keeps both ──────────\033[0m")
    time.sleep(0.3)
    f4, r4 = call("show_stat", key="pricing-revised", value="15%", label="revised growth",
                  revises="pricing")
    show('speaker: "no wait, I had fifteen percent"', f4, r4, quiet)
    check("branch emits block.add + link.add",
          ops_of(f4)[:2] == ["block.add", "link.add"], str(ops_of(f4)))
    check("the original number survives", first_id in canvas.BLOCKS)
    check("both are on the board", len(canvas.BLOCKS) == 2, f"{len(canvas.BLOCKS)}")
    alt = canvas.BLOCKS[r4["block_id"]]
    check("branch sits beside its parent, same row",
          alt.x > canvas.BLOCKS[first_id].x and alt.y == canvas.BLOCKS[first_id].y,
          f"parent=({canvas.BLOCKS[first_id].x},{canvas.BLOCKS[first_id].y}) "
          f"alt=({alt.x},{alt.y})")
    check("branch records what it contradicts", alt.parent == "pricing", str(alt.parent))

    print("\n\033[1m── 5. cooldown suppresses a twitchy re-draw ─────────\033[0m")
    time.sleep(0.3)
    fa5, ra5 = call("show_stat", key="pricing", value="41k", label="monthly burn")
    check("an update past the cooldown window still lands",
          ops_of(fa5)[:1] == ["block.update"], str(ops_of(fa5)))
    f5, r5 = call("show_stat", key="pricing", value="42k", label="monthly burn")
    show("same key again, immediately after", f5, r5, quiet)
    check("no frames emitted while on cooldown", f5 == [], str(ops_of(f5)))
    check("model is told why", r5.get("skipped") == "cooldown", str(r5))

    print("\n\033[1m── 6. a chart FILLS IN, series merged by label ───────\033[0m")
    fa, ra = call("show_chart", key="latency", kind="bar", unit="ms",
                  series=[{"label": "capture", "value": 40}])
    show('speaker: "capture is forty mils"', fa, ra, quiet)
    time.sleep(0.3)
    fb, rb = call("show_chart", key="latency", kind="bar", unit="ms",
                  series=[{"label": "model", "value": 900},
                          {"label": "capture", "value": 45}])
    show('speaker: "...the model is nine hundred, capture is really 45"', fb, rb, quiet)
    series = canvas.BLOCKS[ra["block_id"]].data["series"]
    check("chart updated in place", ops_of(fb)[:1] == ["block.update"], str(ops_of(fb)))
    check("new series appended, existing one revalued",
          [s["label"] for s in series] == ["capture", "model"]
          and series[0]["value"] == 45, str(series))

    print("\n\033[1m── 7. bullets accumulate on a concept card ──────────\033[0m")
    fc, rc = call("show_concept", key="deck", title="Decks are dead weight",
                  bullets=["slow to make"], accent="violet")
    time.sleep(0.3)
    fd, rd = call("show_concept", key="deck", bullets=["slow to make", "locks the talk"])
    show('speaker develops the same point', fd, rd, quiet)
    bullets = canvas.BLOCKS[rc["block_id"]].data["bullets"]
    check("bullet appended, duplicate not repeated",
          bullets == ["slow to make", "locks the talk"], str(bullets))
    check("accent preserved across the update",
          canvas.BLOCKS[rc["block_id"]].data.get("accent") == "violet",
          str(canvas.BLOCKS[rc["block_id"]].data.get("accent")))

    print("\n\033[1m── 8. topics cluster into their own columns ──────────\033[0m")
    cols = {b.cluster: b.x for b in canvas.BLOCKS.values() if "." not in b.key}
    check("each topic claimed a distinct column",
          len(set(cols.values())) == len(cols), str(cols))
    check("three topics tracked", set(cols) == {"pricing", "latency", "deck"}, str(set(cols)))

    print("\n\033[1m── 9. hallucinated block ids do not crash ───────────\033[0m")
    fe, re_ = call("connect_blocks", from_id="b_999", to_id="b_998", label="nope")
    show("model invents two ids", fe, re_, quiet)
    check("bad ids emit nothing and report an error", fe == [] and "error" in re_, str(re_))

    print("\n\033[1m── 10. every result carries the canvas manifest ──────\033[0m")
    check("manifest present in tool result", "canvas" in re_ and re_["canvas"],
          "missing")
    check("manifest exposes key + revisions",
          all({"id", "key", "type", "title", "revisions"} <= set(e)
              for e in re_["canvas"]), str(re_["canvas"][:1]))
    if not quiet:
        print("\n" + canvas.manifest_text())

    print("\n\033[1m── 11. clear resets backend state too ───────────────\033[0m")
    ff, rf = call("clear_canvas")
    check("canvas.clear emitted", ops_of(ff) == ["canvas.clear"], str(ops_of(ff)))
    check("backend registries emptied",
          not canvas.BLOCKS and not canvas.BY_KEY, f"{len(canvas.BLOCKS)} left")


# --- fixture generation -----------------------------------------------------
# demo.jsonl has no block.update frames at all, so the frontend has nothing to
# build the smooth-growth rendering against. This emits a fixture from REAL
# backend output — every frame below is what the canvas actually produces.

STORY = [
    (0.0, "so nobody wants to build slides for a ten minute brainstorm",
     "show_concept", dict(key="deck", title="Nobody builds slides for 10 minutes",
                          bullets=["decks are slow to make"], accent="slate")),
    (2.6, "they're slow to make and they lock the whole talk into a script",
     "show_concept", dict(key="deck",
                          bullets=["decks are slow to make", "locks the talk into a script"])),
    (2.4, "we get from a sentence to pixels in about one and a half seconds",
     "show_stat", dict(key="latency", value="1.4s", label="sentence to pixels")),
    (2.8, "capture is forty mils, the model is about nine hundred, render one fifty",
     "show_chart", dict(key="stages", kind="bar", unit="ms", title="Where the time goes",
                        series=[{"label": "capture", "value": 40},
                                {"label": "model", "value": 900}])),
    (2.2, "and the render is another one hundred and fifty on top of that",
     "show_chart", dict(key="stages", kind="bar", unit="ms",
                        series=[{"label": "render", "value": 150}])),
    (2.7, "the whole loop is just mic, model, canvas",
     "show_diagram", dict(key="pipeline", title="The loop",
                          mermaid="graph LR\n  M[mic] --> G[Gemini Live]\n"
                                  "  G --> C[canvas ops]\n  C --> S[screen]")),
    (3.0, "actually I measured it again this morning, it's more like one point two",
     "show_stat", dict(key="latency-measured", value="1.2s",
                       label="remeasured this morning", revises="latency")),
    (2.9, "I'm at NG Greenhouse and I want to get to Luckin Coffee",
     "show_route", dict(key="coffee", origin="NG Greenhouse",
                        destination="Luckin Coffee", mode="walking")),
]


def emit_fixture(path: str) -> None:
    """Replay STORY through the real dispatcher and write the frames as a fixture."""
    import ops

    canvas.reset()
    canvas.COOLDOWN_S = 0.0            # the story is already paced like real speech
    canvas.FOCUS_THROTTLE_S = 0.0
    out: list[dict] = []

    def rec(delay, frame):
        out.append({"delay": round(delay, 2), "op": frame["op"],
                    "payload": frame["payload"]})

    rec(0.0, ops.canvas_clear())
    rec(0.5, ops.status("listening"))
    for delay, said, tool, args in STORY:
        rec(delay, ops.status("thinking", said))
        frames, result = tools.dispatch(tool, args)
        if not frames:
            print(f"  !! '{tool}' produced nothing: {result.get('error') or result}")
        for i, f in enumerate(frames):
            rec(0.9 if i == 0 else 0.25, f)
    rec(1.5, ops.status("listening"))

    pathlib.Path(path).write_text(
        "\n".join(__import__("json").dumps(o, ensure_ascii=False) for o in out) + "\n")
    kinds: dict[str, int] = {}
    for o in out:
        kinds[o["op"]] = kinds.get(o["op"], 0) + 1
    print(f"wrote {len(out)} frames to {path}")
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print("\n" + canvas.manifest_text())


def reconnect_scenario(quiet: bool) -> None:
    """The most likely on-stage failure: the Live session dies mid-talk.

    The canvas outlives the session. A fresh session that is not re-briefed will
    duplicate everything already on screen. Verified here as far as it can be
    without a live API key — the config validates, and the brief actually names
    every block that is up.
    """
    import gemini_live
    import memory as memory_mod

    canvas.reset()
    canvas.FOCUS_THROTTLE_S = 0.0
    call("show_stat", key="latency", value="1.4s", label="sentence to pixels")
    call("show_concept", key="deck", title="Decks are dead weight",
         bullets=["slow to make"])

    print("\n\033[1m── 12. the Live config the session connects with ────\033[0m")
    cold = gemini_live.build_config(None)
    warm = gemini_live.build_config("handle-abc123")
    check("context compression configured (session survives a long talk)",
          cold.context_window_compression is not None
          and cold.context_window_compression.sliding_window is not None)
    check("session resumption configured (reconnect keeps the conversation)",
          warm.session_resumption is not None
          and warm.session_resumption.handle == "handle-abc123")
    from config import CFG
    check("the ear advertises the full tool set",
          len(cold.tools[0].function_declarations) == len(tools.DECLARATIONS),
          f"{len(cold.tools[0].function_declarations)} vs {len(tools.DECLARATIONS)}")
    check("input transcription on (feeds memory and the ticker)",
          cold.input_audio_transcription is not None)
    check("VAD mode matches MANUAL_ACTIVITY",
          cold.realtime_input_config.automatic_activity_detection.disabled
          == CFG.manual_activity)

    # Every drawing tool must carry the topic key, or the board stops evolving and
    # starts accreting duplicates again. Catches a newly added tool that forgot it.
    drawing = [d for d in tools.DECLARATIONS if d["name"].startswith("show_")]
    missing_key = [d["name"] for d in drawing
                   if "key" not in d["parameters"]["properties"]]
    missing_req = [d["name"] for d in drawing
                   if "key" not in d["parameters"].get("required", [])]
    check(f"all {len(drawing)} show_* tools take a topic key", not missing_key,
          str(missing_key))
    check("...and require it", not missing_req, str(missing_req))
    missing_rev = [d["name"] for d in drawing
                   if "revises" not in d["parameters"]["properties"]]
    check("all show_* tools can branch on contradiction", not missing_rev,
          str(missing_rev))

    print("\n\033[1m── 13. a reconnect is briefed, not amnesiac ─────────\033[0m")
    mem = memory_mod.TopicMemory()
    mem.summary = {"thread": "how fast the screen reacts",
                   "topics": [{"key": "latency", "gist": "sentence to pixels under 2s"}],
                   "numbers": [{"value": "1.4s", "of": "sentence to pixels"}],
                   "decisions": ["ship the mic-first version"],
                   "questions": ["do we need the camera at all"]}
    brief = f"{mem.resume_brief()}\n\n{canvas.manifest_text()}"
    check("brief names every live block key",
          all(k in brief for k in ("latency", "deck")), brief[:120])
    check("brief carries block ids the model can connect to",
          all(b in brief for b in canvas.BLOCKS), "missing ids")
    check("brief carries the prior thread and numbers",
          "how fast the screen reacts" in brief and "1.4s" in brief)
    check("brief states it is resuming, not starting",
          "resuming a talk already in progress" in brief)
    if not quiet:
        print("\n\033[2m" + brief + "\033[0m")

    print("\n\033[1m── 14. after the brief, a known topic UPDATES ───────\033[0m")
    time.sleep(0.3)
    f, r = call("show_stat", key="latency", value="1.2s", label="sentence to pixels")
    show("new session draws about a topic already on screen", f, r, quiet)
    check("it grew the existing block instead of duplicating",
          ops_of(f)[:1] == ["block.update"], str(ops_of(f)))
    check("board still has exactly the two original blocks",
          len(canvas.BLOCKS) == 2, f"{len(canvas.BLOCKS)}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--fixture", metavar="PATH",
                   help="write a mock_server fixture from real backend output")
    a = p.parse_args()
    if a.fixture:
        emit_fixture(a.fixture)
        return 0
    scenario(a.quiet)
    reconnect_scenario(a.quiet)
    print()
    if _fails:
        print(f"\033[31m{len(_fails)} check(s) failed:\033[0m " + "; ".join(_fails))
        return 1
    print("\033[32mall checks passed — the board evolves\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Offline harness: prove the board EVOLVES, with no mic, no API key, no network.

Feeds scripted tool calls straight through tools.dispatch and checks the frames that
come out. This is the fast loop for tuning canvas.py — a talk takes 90 seconds to
rehearse, this takes one.

    python backend/replay.py            # run the scenario, assert, exit non-zero on fail
    python backend/replay.py --quiet    # just the verdict
"""
import argparse
import pathlib
import re
import sys
import time

import canvas
import tools
from config import CFG

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
    canvas.STAGE_MAX_LIVE = 99        # these checks are about merge/branch logic;
    canvas.STAGE_LIFETIME_S = 9e9     # stage retirement is covered separately below
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

    # A fixture generated from a live run bakes CFG.maps_key into every embed_url.
    # That is exactly how a live key reached public main twice. The stage
    # substitutes a real one from ?mapskey= at demo time, so redact here always.
    blob = "\n".join(__import__("json").dumps(o, ensure_ascii=False) for o in out)
    if CFG.maps_key:
        blob = blob.replace(CFG.maps_key, "YOUR_MAPS_KEY")
    leaked = re.findall(r"AIzaSy[A-Za-z0-9_\-]{20,}|AQ\.[A-Za-z0-9_\-]{20,}", blob)
    if leaked:
        raise SystemExit(f"refusing to write {path}: it still contains "
                         f"{len(leaked)} key-shaped string(s)")
    pathlib.Path(path).write_text(blob + "\n")
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
    canvas.STAGE_MAX_LIVE, canvas.STAGE_LIFETIME_S = 99, 9e9
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


def stage_scenario(quiet: bool) -> None:
    """The stage retires scenes on its own. canvas.py must forget them too, or the
    manifest tells the model about blocks the room can no longer see."""
    canvas.reset()
    canvas.COOLDOWN_S = 0.0
    canvas.FOCUS_THROTTLE_S = 0.0
    canvas.STAGE_MAX_LIVE = 3
    canvas.STAGE_LIFETIME_S = 0.4

    print("\n\033[1m── 15. only MAX_LIVE scenes are reported ────────────\033[0m")
    # Deliberately unrelated names: 'topic1'/'topic0' score 0.83 on the fuzzy key
    # match and would legitimately collapse into one block.
    names = ["pricing", "latency", "hiring", "market", "roadmap"]
    for n in names:
        call("show_stat", key=n, value="1", label=n)
    m = canvas.manifest()
    check("manifest never claims more than the stage shows", len(m) == 3, str(len(m)))
    check("it keeps the NEWEST three", [e["key"] for e in m]
          == names[:1:-1], str([e["key"] for e in m]))

    print("\n\033[1m── 16. a retired topic can be drawn again ───────────\033[0m")
    time.sleep(0.5)                      # everything ages past STAGE_LIFETIME_S
    check("nothing is reported as on screen", canvas.manifest() == [],
          str(canvas.manifest()))
    f, r = call("show_stat", key="roadmap", value="9", label="roadmap")
    show("speaker returns to a topic that scrolled off", f, r, quiet)
    check("it is re-ADDED, not silently updated into a dead block",
          ops_of(f)[:1] == ["block.add"], str(ops_of(f)))


def trace_scenario(quiet: bool) -> None:
    """Every stage can decide not to draw. This asserts that each of those decisions
    says so on the wire, because a silent skip and a broken pipeline are the same
    thing from the audience's seat — and were the same thing in the logs too.
    """
    import ears_local
    import ops
    import preflight
    import vitals

    seen: list[dict] = []
    vitals.BROADCAST = lambda frame: (seen.append(frame["payload"])
                                      if frame["op"] == "trace" else None)
    vitals.reset()
    canvas.reset()
    canvas.COOLDOWN_S = 6.0
    canvas.FORM_LOCK_S = 12.0
    canvas.FOCUS_THROTTLE_S = 0.0
    canvas.STAGE_MAX_LIVE, canvas.STAGE_LIFETIME_S = 99, 9e9

    def last(stage: str | None = None) -> dict:
        for p in reversed(seen):
            if stage is None or p["stage"] == stage:
                return p
        return {}

    print("\n\033[1m── 17. a drawn block is traced ──────────────────────\033[0m")
    call("show_stat", key="pricing", value="40k", label="monthly burn")
    t = last("tool")
    check("a successful draw traces tool/ok", t.get("verdict") == "ok", str(t))
    check("the trace names the tool and what it did",
          "show_stat" in t.get("reason", "") and "add" in t.get("reason", ""),
          t.get("reason", ""))

    print("\n\033[1m── 18. a cooldown says so, and names itself ─────────\033[0m")
    frames, result = call("show_stat", key="pricing", value="41k", label="burn")
    show("the same topic again, immediately", frames, result, quiet)
    t = last("tool")
    check("a cooldown traces tool/block", t.get("verdict") == "block", str(t))
    check("the reason names COOLDOWN_S and its value",
          "cooldown" in t.get("reason", "") and "6.0" in t.get("reason", ""),
          t.get("reason", ""))
    check("nothing reached the screen", frames == [], str(ops_of(frames)))

    print("\n\033[1m── 19. a refused form change says so ────────────────\033[0m")
    # A different SHAPE for a topic that is already up. The cooldown must not be
    # what catches this, or the trace would name the wrong rule.
    canvas.COOLDOWN_S = 0.0
    frames, result = call("show_concept", key="pricing", title="Pricing",
                          bullets=["it is complicated"])
    t = last("tool")
    check("a form change traces tool/block", t.get("verdict") == "block", str(t))
    check("the reason names the form lock", "form" in t.get("reason", "").lower(),
          t.get("reason", ""))

    print("\n\033[1m── 20. a bad call is not silent ─────────────────────\033[0m")
    call("show_nothing_at_all", key="x")
    check("an unknown tool traces tool/error", last("tool").get("verdict") == "error",
          str(last("tool")))
    n = len(seen)
    call("show_route", key="route-x")             # a new block missing its required
    check("missing fields trace an error too",                 # fields
          any(p["verdict"] == "error" for p in seen[n:]), str(seen[n:]))

    print("\n\033[1m── 21. an asset that never arrives is admitted ──────\033[0m")
    sent: list[dict] = []
    tools.BROADCAST = sent.append
    tools._asset_failed("b_99", "image generation failed", detail="quota")
    check("the block is told it failed",
          [f["payload"].get("data", {}).get("failed") for f in sent]
          == ["image generation failed"], str(sent))
    check("and it traces asset/error", last("asset").get("verdict") == "error",
          str(last("asset")))
    tools.BROADCAST = None

    print("\n\033[1m── 22. a flood is coalesced, not dropped ────────────\033[0m")
    n = len(seen)
    for _ in range(40):
        vitals.trace("mic", "drop", "below MIC_GATE 0.014", throttle=9e9)
    burst = seen[n:]
    check("40 identical events become one frame", len(burst) == 1, str(len(burst)))
    check("the first of a burst is the one that gets through",
          burst[0]["reason"].startswith("below MIC_GATE") if burst else False,
          str(burst))
    check("but the count is still honest",
          vitals.COUNTS["mic.drop"] >= 40, str(vitals.COUNTS["mic.drop"]))

    print("\n\033[1m── 23. the wire refuses a malformed trace ───────────\033[0m")
    for bad in (("nowhere", "ok"), ("ear", "shrug")):
        try:
            ops.trace(*bad, "reason")
            check(f"{bad} is rejected", False, "it was accepted")
        except ValueError:
            check(f"{bad!r} is rejected before it reaches a client", True)

    print("\n\033[1m── 24. the stage owns its own capacity ──────────────\033[0m")
    check("the display's policy is adopted",
          canvas.set_stage_policy(1, 26000) == {"max_live": 1, "lifetime_s": 26.0},
          str(canvas.set_stage_policy(1, 26000)))
    check("a nonsense capacity is clamped, not obeyed",
          canvas.set_stage_policy(0, 1)["max_live"] == 1)
    canvas.set_stage_policy(3, 26000)
    canvas.reset()
    for k in ("a", "b", "c", "d"):
        call("show_stat", key=k, value="1", label=k)
    check("the manifest matches what the display said it can hold",
          len(canvas.manifest()) == 3, str(len(canvas.manifest())))

    print("\n\033[1m── 25. Whisper's inventions never reach the room ────\033[0m")
    check("a stock phrase from silence is caught",
          ears_local._invented("Thank you."))
    check("a looped phrase is caught",
          ears_local._invented("Thanks for watching. Thanks for watching. "
                               "Thanks for watching."))
    check("real speech is not", not ears_local._invented(
        "our latency is 1.4 seconds from sentence to pixels"))

    print("\n\033[1m── 26. preflight reports a failure, not a shrug ─────\033[0m")
    check("a FAIL row is a blocking exit code",
          preflight.exit_code([(preflight.PASS, "a", ""),
                               (preflight.FAIL, "b", "")]) == 1)
    check("a WARN row is not", preflight.exit_code(
        [(preflight.PASS, "a", ""), (preflight.WARN, "b", "")]) == 0)
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        held = s.getsockname()[1]
        check("a port already in use is detected",
              not preflight._port_free("127.0.0.1", held))

    vitals.BROADCAST = None


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
    stage_scenario(a.quiet)
    trace_scenario(a.quiet)
    print()
    if _fails:
        print(f"\033[31m{len(_fails)} check(s) failed:\033[0m " + "; ".join(_fails))
        return 1
    print("\033[32mall checks passed — the board evolves\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A brain that needs no LLM at all.

When the Gemini account is capped, transcription still works (the browser does
it for free) but nothing reaches the screen, which looks exactly like a broken
product. This picks scenes straight from the transcript with plain pattern
matching, so a talk still draws.

It has none of Gemini's judgement -- no sense of what deserves the screen, no
memory of the argument, no taste about form. It is a floor, not a replacement.
The tools it drives are real: Wikimedia photo search and Google Maps routes
need no Gemini quota, so those scenes are genuinely live.

    python backend/main.py --local-brain
"""
import asyncio
import re

import tools

# Spoken numbers, so "one point four seconds" and "nine hundred" both land.
_WORD_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
}

_STOP = {
    "the", "a", "an", "and", "or", "but", "so", "then", "that", "this", "it",
    "is", "are", "was", "were", "be", "been", "to", "of", "in", "on", "at",
    "for", "with", "as", "we", "i", "you", "they", "our", "your", "just",
    "really", "actually", "basically", "like", "about", "here", "there",
    "what", "when", "how", "now", "okay", "right", "well", "yeah", "going",
}

_PHOTO_CUE = re.compile(
    r"\b(?:looks? like|as clean as|picture (?:a|an|the)|show (?:me )?(?:a|an|the)"
    r"|imagine (?:a|an|the))\s+(.{3,48})", re.I)
_IMAGINE_CUE = re.compile(r"\b(?:imagine|picture|envision|visuali[sz]e)\b", re.I)
# "from X to Y" alone is far too loose: "from a spoken sentence to pixels"
# became a walking route. Require a movement verb somewhere in the line.
_MOVE_CUE = re.compile(
    r"\b(?:walk|drive|cycle|get|go|travel|head|route|directions?|far)\b", re.I)
_ROUTE_CUE = re.compile(
    r"\b(?:i'?m at|from)\s+(.{2,40}?)\s+"
    r"(?:to|towards?|over to)\s+(.{2,40}?)(?:[.,!?]|$)", re.I)
# filler that clings to a captured place name
_PLACE_TRIM = re.compile(
    r"^(?:the|a|an|and|i|we)\s+|\s+(?:and|but|so|then|now|right|i|we)"
    r"(?:\s+\w+)*$", re.I)


def _clean_place(p: str) -> str:
    p = p.strip(" .,!?")
    for _ in range(3):
        new = _PLACE_TRIM.sub("", p).strip(" .,")
        if new == p:
            break
        p = new
    # "get to Fusionopolis" -> "Fusionopolis"
    p = re.sub(r"^(?:get|go|walk|drive|head)\s+(?:to\s+)?", "", p, flags=re.I)
    return p.strip(" .,")
_MATH_CUE = re.compile(
    r"\b(\w+)\s+(?:equals|is equal to)\s+(.{2,60}?)(?:[.,!?]|$)", re.I)
_SUMMARY_CUE = re.compile(
    r"\b(?:to sum up|in summary|to summari[sz]e|to wrap up|that'?s it|"
    r"in conclusion|so overall)\b", re.I)


def _numbers(text: str) -> list[tuple[str, float]]:
    """Every figure in the line, labelled by the nearest meaningful word before
    it. Taking the immediately preceding word gave "capture is 40 ms" the label
    "is" -- and then the unit, so every bar was called "ms"."""
    out: list[tuple[str, float]] = []
    for m in re.finditer(r"\$?(\d[\d,]*\.?\d*)\s*"
                         r"(%|percent|ms|milliseconds?|seconds?|k|km|min|minutes?)?",
                         text, re.I):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        before = re.findall(r"[A-Za-z][A-Za-z'-]+", text[:m.start()])
        label = next((w for w in reversed(before)
                      if w.lower() not in _STOP and len(w) > 2), "")
        out.append((label.lower() or (m.group(2) or "value"), val))
    return out


def _keywords(text: str, n: int = 4) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", text)
             if w.lower() not in _STOP and len(w) > 2]
    return words[:n]


def _emoji_for(text: str) -> str:
    t = text.lower()
    for cue, glyph in (
        ("time", "⏱️"), ("fast", "⚡"), ("speed", "⚡"), ("second", "⏱️"),
        ("money", "💰"), ("cost", "💰"), ("price", "💰"), ("grow", "📈"),
        ("user", "👥"), ("team", "👥"), ("people", "👥"), ("problem", "⚠️"),
        ("risk", "⚠️"), ("idea", "💡"), ("build", "🔨"), ("ship", "🚀"),
        ("launch", "🚀"), ("data", "📊"), ("slide", "🗓️"), ("screen", "🖥️"),
        ("voice", "🎙️"), ("map", "🗺️"), ("photo", "🖼️"), ("model", "🧠"),
    ):
        if cue in t:
            return glyph
    return "💡"


class LocalBrain:
    """Same surface as Brain: .feed(text) and .set_enabled(bool)."""

    def __init__(self, broadcast):
        self.broadcast = broadcast
        self.enabled = True
        self._n = 0
        self._said: list[str] = []          # for the closing recap
        self._stats: list[tuple[str, float]] = []
        self._speaker = ""
        # server.py reports this to the screen so the dock can say what is
        # actually driving the canvas.
        self.model = "local (no LLM)"

    # --- same surface as Brain, so main.py can swap one for the other ---

    def set_speaker(self, label: str) -> None:
        """Who has the floor. Recorded for the recap; this brain does not
        change what it draws based on who is talking."""
        self._speaker = label

    async def loop(self) -> None:
        """Brain runs a debounce loop; this one decides synchronously in feed(),
        so there is nothing to pump. Still awaited by main.py."""
        while True:
            await asyncio.sleep(3600)

    async def set_enabled(self, on: bool) -> None:
        self.enabled = on
        print(f"[local-brain] {'ENABLED' if on else 'disabled'}")

    def _emit(self, tool: str, **args) -> None:
        frames, result = tools.dispatch(tool, args)
        if not frames:
            note = result.get("error") or result.get("skipped")
            if note:
                print(f"[local-brain] {tool}: {note}")
            return
        for f in frames:
            self.broadcast(f)
        print(f"[local-brain] {tool}")

    def feed(self, text: str) -> None:
        if not self.enabled:
            return
        line = " ".join(text.split())
        if len(line) < 8:
            return
        self._said.append(line)
        self._n += 1
        key = f"k{self._n}"

        # 1. Wrap-up cue closes the talk with everything said so far.
        if _SUMMARY_CUE.search(line):
            items = [{"emoji": _emoji_for(s), "label": " ".join(_keywords(s, 3))}
                     for s in self._said[:-1][-6:]]
            for lbl, val in self._stats[:2]:
                items.insert(0, {"value": f"{val:g}", "label": lbl})
            if items:
                self._emit("show_summary", key="summary", title="In summary",
                           items=items[:8])
            return

        # 2. A route beats everything else in the line.
        m = _ROUTE_CUE.search(line)
        if m and _MOVE_CUE.search(line):
            origin, dest = _clean_place(m.group(1)), _clean_place(m.group(2))
            # A place is a name, not a measurement. "we get from a spoken
            # sentence to pixels in about 1.4 seconds" has a movement verb and
            # a from/to, and is not a journey — the digits give it away.
            if origin and dest and not re.search(r"\d", origin + dest):
                self._emit("show_route", key=key, origin=origin,
                           destination=dest, mode="walking")
                return

        # 3. "y equals w x squared over two H"
        m = _MATH_CUE.search(line)
        if m and not re.fullmatch(r"[\d\s.,]+", m.group(2)):
            rhs = (m.group(2).replace(" squared", "^2").replace(" over ", " / ")
                             .replace(" times ", " \\cdot "))
            self._emit("show_math", key=key, tex=f"{m.group(1)} = {rhs}")
            return

        # 4. A named thing to look at.
        m = _PHOTO_CUE.search(line)
        if m:
            subject = re.sub(r"\b(?:at|in|with|during)\b.*$", "", m.group(1)).strip(" .,")
            if subject:
                tool = "show_image" if _IMAGINE_CUE.search(line) and len(
                    subject.split()) > 3 else "show_photo"
                if tool == "show_photo":
                    self._emit("show_photo", key=key, query=subject)
                else:
                    self._emit("show_image", key=key, prompt=subject)
                return

        # 5. Numbers: several make a chart, one makes a stat.
        nums = _numbers(line)
        if len(nums) >= 3:
            self._stats.extend(nums[:1])
            self._emit("show_chart", key=key, kind="bar",
                       title=" ".join(_keywords(line, 3)) or "Figures",
                       series=[{"label": l, "value": v} for l, v in nums[:6]])
            return
        if len(nums) == 1:
            label, val = nums[0]
            self._stats.append((label, val))
            self._emit("show_stat", key=key, value=f"{val:g}", label=label)
            return

        # 6. Otherwise: an emoji and a few words. Only for a substantial line —
        #    filler should leave the screen alone.
        kws = _keywords(line, 4)
        if len(kws) >= 2:
            self._emit("show_hero", key=key, emoji=_emoji_for(line),
                       title=" ".join(kws[:4]))

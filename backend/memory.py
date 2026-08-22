"""Durable topic memory — the net under the Live session.

The Live session is the PRIMARY context holder: it keeps the verbatim audio and the
tool history, and with compression + resumption on it survives a long talk. This
module is the durable layer beside it, and it exists for three things the session
cannot do:

  1. survive its own death — a reconnected session wakes up blank while the canvas
     still shows a dozen blocks
  2. survive the process dying — `.session/memory.json`
  3. answer "what did we say about pricing earlier?" later on

It runs on CFG.model (gemini-3.7-flash), strictly OFF the hot path, on a slow timer.
It must never be able to break the demo: every failure keeps the previous summary and
logs. Nothing here is ever awaited by the audio pipeline.
"""
import asyncio
import json
import re
import time
from collections import deque
from pathlib import Path

from google import genai
from google.genai import types

from config import CFG

INTERVAL_S = 30.0
MIN_NEW_CHARS = 120          # don't burn a call on "so, uh, right"
STATE_DIR = Path(__file__).resolve().parent.parent / ".session"
STATE_FILE = STATE_DIR / "memory.json"

SYSTEM = """\
You maintain the running memory of a live talk. You are given the previous memory, \
the newest speech, and what is currently on the presentation canvas.

Return ONLY a JSON object with exactly these keys:
  "thread"    : one short sentence — what is being discussed RIGHT NOW
  "topics"    : [{"key": <canvas key if the topic has a block, else a slug>,
                  "gist": <under 12 words>}]
  "numbers"   : [{"value": <as spoken>, "of": <what it measures>}]
  "decisions" : [<short strings — things the room settled>]
  "questions" : [<short strings — things raised and not resolved>]

Rules:
- Carry forward everything from the previous memory that is still true. This is a
  running document, not a summary of the last 30 seconds.
- Reuse the canvas `key` for a topic that has a block, so memory and screen agree.
- Never invent a number, name, or decision that was not said.
- If two statements about the same quantity conflict, keep BOTH in "numbers".
- Keep it tight: at most 8 topics, 8 numbers, 5 decisions, 5 questions.
"""

EMPTY = {"thread": "", "topics": [], "numbers": [], "decisions": [], "questions": []}


def _parse(text: str) -> dict | None:
    """Tolerate fences and stray prose around the JSON."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


class TopicMemory:
    def __init__(self) -> None:
        self.summary: dict = dict(EMPTY)
        self.started = time.time()
        self._frag: deque[str] = deque(maxlen=4000)
        self._total = 0          # monotonic fragment count, survives deque eviction
        self._consumed = 0       # measured against _total, not against the deque
        self._client: genai.Client | None = None
        self._runs = 0

    # --- ingest (called from the hot path; must stay trivial) ---------------
    def add_utterance(self, text: str) -> None:
        """Live input_transcription arrives in fragments. Just accumulate."""
        if text:
            self._frag.append(text)
            self._total += 1

    def _new_text(self) -> str:
        frags = list(self._frag)
        # The deque drops the oldest fragments once full; translate the monotonic
        # consumed-count into an offset into what is actually still held.
        dropped = self._total - len(frags)
        return "".join(frags[max(0, self._consumed - dropped):]).strip()

    # --- the slow loop ------------------------------------------------------
    async def loop(self) -> None:
        if not CFG.api_key:
            print("[mem] no API key; topic memory disabled")
            return
        self._load()
        self._client = genai.Client(api_key=CFG.api_key)
        while True:
            await asyncio.sleep(INTERVAL_S)
            try:
                await self._tick()
            except Exception as e:  # noqa: BLE001 - never take the demo down
                print(f"[mem] tick failed, keeping previous memory: "
                      f"{type(e).__name__}: {e}")

    async def _tick(self) -> None:
        import canvas  # local import: canvas must not depend on memory

        new = self._new_text()
        if len(new) < MIN_NEW_CHARS:
            return
        prompt = (
            f"PREVIOUS MEMORY:\n{json.dumps(self.summary, ensure_ascii=False)}\n\n"
            f"{canvas.manifest_text()}\n\n"
            f"NEWEST SPEECH:\n{new}"
        )
        resp = await self._client.aio.models.generate_content(
            model=CFG.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        parsed = _parse(getattr(resp, "text", "") or "")
        if not parsed:
            print("[mem] unparseable summary, keeping previous")
            return
        self.summary = {k: parsed.get(k, EMPTY[k]) for k in EMPTY}
        self._consumed = self._total
        self._runs += 1
        self._save()
        print(f"[mem] #{self._runs} thread={self.summary.get('thread', '')!r} "
              f"topics={len(self.summary.get('topics', []))}")

    # --- reads --------------------------------------------------------------
    def resume_brief(self) -> str:
        """Injected into a fresh Live session so a reconnect is not an amnesia."""
        s = self.summary
        mins = int((time.time() - self.started) // 60)
        lines = [
            "You are resuming a talk already in progress "
            f"(~{mins} min in). You did not hear the earlier part; this is the record."
        ]
        if s.get("thread"):
            lines.append(f"CURRENTLY DISCUSSING: {s['thread']}")
        if s.get("topics"):
            lines.append("COVERED SO FAR:")
            lines += [f"  - {t.get('key', '?')}: {t.get('gist', '')}"
                      for t in s["topics"]]
        if s.get("numbers"):
            lines.append("NUMBERS STATED: " + "; ".join(
                f"{n.get('value')} ({n.get('of')})" for n in s["numbers"]))
        if s.get("decisions"):
            lines.append("DECIDED: " + "; ".join(s["decisions"]))
        if s.get("questions"):
            lines.append("STILL OPEN: " + "; ".join(s["questions"]))
        tail = self._new_text()[-600:]
        if tail:
            lines.append(f"MOST RECENT WORDS: ...{tail}")
        return "\n".join(lines)

    def recall(self, query: str) -> list[dict]:
        """Cheap keyword recall over topics and numbers. Backs 'what did we say about X'."""
        q = {w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 3}
        if not q:
            return []
        hits = []
        for t in self.summary.get("topics", []):
            blob = f"{t.get('key', '')} {t.get('gist', '')}".lower()
            if q & set(re.split(r"\W+", blob)):
                hits.append({"kind": "topic", **t})
        for n in self.summary.get("numbers", []):
            if q & set(re.split(r"\W+", str(n.get("of", "")).lower())):
                hits.append({"kind": "number", **n})
        return hits

    # --- persistence --------------------------------------------------------
    def _save(self) -> None:
        try:
            STATE_DIR.mkdir(exist_ok=True)
            STATE_FILE.write_text(json.dumps(
                {"started": self.started, "summary": self.summary},
                indent=2, ensure_ascii=False))
        except OSError as e:
            print(f"[mem] could not persist: {e}")

    def _load(self) -> None:
        """Pick a talk back up after a full process restart."""
        try:
            if not STATE_FILE.exists():
                return
            blob = json.loads(STATE_FILE.read_text())
            age = time.time() - blob.get("started", 0)
            if age > 3600:          # stale: a different talk
                return
            self.summary = {k: blob.get("summary", {}).get(k, EMPTY[k]) for k in EMPTY}
            self.started = blob.get("started", self.started)
            print(f"[mem] resumed memory from disk ({int(age)}s old)")
        except (OSError, json.JSONDecodeError) as e:
            print(f"[mem] could not load prior memory: {e}")

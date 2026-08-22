# Phone mics — up to four people, one canvas

The room is the microphone, not one laptop. Each person opens a URL on their phone
and becomes a mic feeding the same canvas.

```
phone browser ──┐
phone browser ──┤  wss, binary 20 ms PCM
phone browser ──┼──► mic_server ──► Floor ──► queue ──► Live API ──► canvas ops
phone browser ──┤                  (one speaker
Mac's own mic ──┘                   at a time)
```

Nothing about the contract changes. Mics are an input concern; the frontend never
knows how many there are.

---

## Joining

Start the backend as usual:

```bash
python backend/main.py
```

It prints a URL and a scannable QR code:

```
[mic] phones join at:  https://10.130.0.20:8766/
[mic] up to 4 mics. Accept the certificate warning once.
```

On each phone: same Wi-Fi as the Mac → open the URL → **accept the certificate
warning** → type a name → *Go live*.

The name matters: it is what the model uses to attribute an idea to a person.

### Why the certificate warning

Browsers only allow microphone access in a *secure context*. A phone on
`http://10.130.0.20` gets no mic at all — not a permission prompt, just nothing. So
the page is served over HTTPS with a self-signed certificate generated on first run
into `.session/mic-cert.pem`.

The warning is expected and it is safe here: the cert is one you just generated, on
your own LAN, for your own machine. Tap through it once per phone.

- **iOS Safari**: "Show Details" → "visit this website" → "Visit Website"
- **Android Chrome**: "Advanced" → "Proceed to … (unsafe)"

This keeps you off tunnels and accounts, and it works with no internet at all.

---

## How four mics become one stream

We do **not** mix the mics. Four phones in a room is four noise floors, and two
phones near each other hear the same voice twice a few milliseconds apart — the
echo/cross-talk risk IDEA.md warns about. Mixing hands the model mush.

Instead, exactly one mic holds the **floor** at any instant, and only its audio is
forwarded:

- A mic takes the floor when it goes above the speech gate and nobody else holds it.
- It **keeps** the floor through a short hangover after going quiet, so word endings
  and pauses mid-sentence are never clipped.
- A **clearly louder** mic can steal the floor immediately — that is what makes an
  unplanned interjection land instead of being swallowed.
- When everyone stops, the floor is released.

Selection is a comparison of frame energy, so it costs nothing: no resampling, no
summing, no added delay.

Knobs live at the top of `backend/mics.py`:

| Constant | Default | Raise it if… | Lower it if… |
|---|---|---|---|
| `GATE_RMS` | `0.010` | room noise keeps opening mics | quiet talkers get ignored |
| `HANGOVER_S` | `0.70` | speech gets cut off between phrases | handoffs feel sluggish |
| `STEAL_MARGIN` | `1.8` | people interrupt each other too easily | interjections don't get through |
| `MAX_MICS` | `4` | — | — |

The fifth phone to connect is refused with a readable message rather than silently
dropped.

---

## Attribution

When the floor changes hands the model is told, as context rather than as a prompt:

```
[Yufei is now the one speaking]
```

It arrives via `send_client_content(..., turn_complete=False)`, **not**
`send_realtime_input(text=...)` — realtime text counts as a user turn and would make
the model answer instead of just listening.

That one line is what lets the system prompt's multi-speaker rules work:

- two people making the same point → **one** block, same `key`, no duplicate
- two people disagreeing → `revises`, both positions side by side, nothing overwritten
- attribution on screen only where it is the point — a decision, a commitment, a
  contested number

If it ever disturbs turn-taking, set `ANNOUNCE_SPEAKERS=0` and everything else keeps
working; you just lose attribution.

---

## Latency

| Hop | Cost |
|---|---|
| phone mic hardware + `getUserMedia` | ~10–25 ms |
| resample to 16 kHz + int16 pack (AudioWorklet, audio thread) | ~1 frame (20 ms) |
| wss over LAN, 640-byte binary frame | ~5–25 ms |
| gate + queue on the Mac | **0.2 ms measured** |

Roughly 40–70 ms from phone to the Live API — under the mic hardware latency it
rides on, and far under the transcription time that follows it. Choices that matter:

- **binary frames**, not base64 — a third fewer bytes and no encode on the phone's
  main thread
- **AudioWorklet**, not `ScriptProcessor` — capture stays on the audio thread, so a
  busy UI cannot add jitter
- **`AudioContext({sampleRate: 16000})`** — the browser resamples natively; the
  worklet falls back to linear interpolation if a browser refuses the rate
- **nothing is buffered server-side** — a frame is gated and queued in the callback
  it arrives in

---

## Flags

```bash
python backend/main.py                 # Mac mic + phones (default)
python backend/main.py --phones-only   # ignore the Mac's mic
python backend/main.py --no-phones     # Mac mic only, no mic server
python backend/main.py --device 2      # pick which Mac input to use
python backend/main.py --pcm talk.pcm  # replay a recording; mic server off
```

Env (`.env`): `MIC_PORT` (8766), `MIC_HOST` (0.0.0.0), `ANNOUNCE_SPEAKERS` (1).

Once mics are connected, a roster line shows who is live and who has the floor —
use it to confirm all four phones are actually up before you start talking:

```
[mic] ▶Marwin ▅ |  Till ▁ |  Philipp ▂ |  Yufei ▁
```

---

## Verify it without phones

```bash
python backend/mic_check.py
```

Starts the real TLS server, connects four synthetic clients over real `wss`, pushes
real PCM through the real floor controller, and measures the server hop. Covers the
page, the 4-mic cap, silence suppression, cross-talk, interruption, hangover
release, slot reuse, and latency.

---

## When it goes wrong

| Symptom | Cause |
|---|---|
| "No mic access" and the URL starts `http://` | You opened the wrong URL. It must be the `https://` one the Mac printed. |
| Page won't load at all | Phone is on a different network — guest Wi-Fi and cellular are the usual culprits. Turn cellular data off. |
| Connects, but the level ring never moves | Permission was denied earlier. Reset it in site settings, or use a fresh private tab. |
| Two phones, doubled/echoey audio | They are too close. Separate them, use headsets, or have one person stop. |
| Someone can't get a word in | Lower `STEAL_MARGIN`. |
| A mic keeps opening on room noise | Raise `GATE_RMS`. |
| Nothing reaches the model | Check the roster line — if no mic shows `▶`, everyone is under the gate. |
| Phone screen sleeps mid-demo | The page requests a wake lock, but iOS may refuse it. Set auto-lock to Never. |

`.session/` holds the generated cert and is gitignored. Delete it to regenerate —
which you must do if the Mac's LAN IP changes, since the IP is in the cert.

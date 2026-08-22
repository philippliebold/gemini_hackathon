"""Find out before the talk, not during it.

    python backend/main.py --check

Every line here is a failure that has actually cost a rehearsal: a key with no
billing, a Whisper model that was never downloaded, a mic that the OS never granted
permission to, a port still held by a backend from twenty minutes ago. Each one
looks identical from the stage — a screen that does nothing — so they are worth two
seconds of checking up front.

Nothing here is a substitute for `replay.py`, which tests the drawing logic. This
tests the machine and the account.
"""
import asyncio
import socket
import time

from config import CFG

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

_MARK = {PASS: "\u2713", WARN: "!", FAIL: "\u2717"}


def _row(verdict: str, label: str, detail: str = "") -> tuple[str, str, str]:
    print(f"  {_MARK[verdict]} {verdict:4}  {label:34}  {detail}")
    return verdict, label, detail


def exit_code(rows: list[tuple[str, str, str]]) -> int:
    """A FAIL is blocking, a WARN is not. Kept separate from the printing so the
    offline harness can assert that a failure is reported rather than swallowed —
    which is the whole point of a preflight."""
    return 1 if any(r[0] == FAIL for r in rows) else 0


def _port_free(host: str, port: int) -> bool:
    """A port in use is almost always a backend somebody forgot to stop, which then
    silently loses every frame to the wrong process."""
    fam = socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


async def _check_models() -> tuple[str, str]:
    """Reuses the brain's own probe, so this measures exactly what the talk will
    use — including the latency bar the chain is judged against."""
    import brain as brain_mod
    b = brain_mod.Brain(lambda frame: None)
    t0 = time.time()
    found = await b.select_model()
    if not found:
        return FAIL, "no model in the chain answered — check billing"
    tag = "primary" if b.model == CFG.model else "FALLBACK"
    return (PASS if b.model == CFG.model else WARN,
            f"{b.model} ({tag}) in {time.time()-t0:.1f}s")


async def _check_ear() -> tuple[str, str]:
    import ears_local
    ear = ears_local.LocalEar(lambda frame: None)
    t0 = time.time()
    if await ear.warm():
        return PASS, f"{ears_local.MODEL} loaded in {time.time()-t0:.1f}s"
    import vitals
    why = (vitals.STATE.get("ear_error") or "failed to load")[:90]
    return FAIL, f"{ears_local.MODEL}: {why}"


async def run() -> int:
    """Print the report. Returns a process exit code: 0 unless something failed."""
    print("\nPreflight\n")
    rows: list[tuple[str, str, str]] = []

    # --- the account ---------------------------------------------------------
    if not CFG.api_key:
        rows.append(_row(FAIL, "GEMINI_API_KEY",
                         "not set — cp .env.example .env and fill it in"))
    else:
        rows.append(_row(PASS, "GEMINI_API_KEY",
                         f"set ({len(CFG.api_key)} chars)"))
        try:
            verdict, detail = await _check_models()
        except Exception as e:                                # noqa: BLE001
            verdict, detail = FAIL, f"{type(e).__name__}: {e}"[:90]
        rows.append(_row(verdict, "Gemini text model", detail))

    # --- the ear -------------------------------------------------------------
    try:
        verdict, detail = await _check_ear()
    except Exception as e:                                    # noqa: BLE001
        verdict, detail = FAIL, f"{type(e).__name__}: {e}"[:90]
    rows.append(_row(verdict, "Whisper (the default ear)", detail))

    # --- the machine ---------------------------------------------------------
    try:
        import audio
        devs = audio.input_devices()
        default = next((d["name"] for d in devs if d["default"]), None)
        if not devs:
            rows.append(_row(FAIL, "Audio input devices",
                             "none — check macOS microphone permission"))
        else:
            rows.append(_row(PASS, "Audio input devices",
                             f"{len(devs)} found"
                             + (f", default: {default}" if default else "")))
    except Exception as e:                                    # noqa: BLE001
        rows.append(_row(FAIL, "Audio input devices",
                         f"{type(e).__name__}: {e}"[:90]))

    for label, host, port in (("WebSocket port", CFG.ws_host, CFG.ws_port),
                              ("Phone-mic port", CFG.mic_host, CFG.mic_port)):
        free = _port_free(host, port)
        rows.append(_row(PASS if free else WARN, label,
                         f"{host}:{port} free" if free
                         else f"{host}:{port} in use — is a backend already running?"))

    # --- optional ------------------------------------------------------------
    rows.append(_row(PASS if CFG.maps_key else WARN, "Google Maps key",
                     f"set, biasing to {CFG.maps_region}" if CFG.maps_key
                     else "not set — show_route draws a straight line instead"))

    failed = [r for r in rows if r[0] == FAIL]
    warned = [r for r in rows if r[0] == WARN]
    print()
    if exit_code(rows):
        print(f"  {len(failed)} blocking problem(s). The talk will not work "
              f"until these are fixed.\n")
        return 1
    print(f"  Ready{f' — {len(warned)} warning(s)' if warned else ''}.\n")
    return 0


def main() -> int:
    return asyncio.run(run())

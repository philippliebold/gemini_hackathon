"""One switch the whole pipeline respects.

Stop means stop: no transcription, no drawing decisions, no summariser ticks. Not
"ignore the results" — the calls are never made. A demo machine left running with a
hot mic otherwise bills quietly in the background, and that is a bad surprise.
"""
LISTENING = True


def set_listening(on: bool) -> bool:
    global LISTENING
    LISTENING = bool(on)
    print(f"[run] {'LISTENING' if LISTENING else 'STOPPED — no API calls'}")
    return LISTENING


def listening() -> bool:
    return LISTENING

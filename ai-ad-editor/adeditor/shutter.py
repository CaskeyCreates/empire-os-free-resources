"""The shutter flash and the sound events for every picture change, kept out of page.py.

A visible punch change (the camera jumping in or back out) gets a white flash over 5 frames, the new shot
punching 1.045 and settling over 8, and a `cut` sound on the whitest frame. A change hidden under B-roll,
a graphic or the end card is neither seen nor heard. The timing is a camera shutter: each cut is keyed on
its first grid frame c, the white runs from c - 1 and the settle starts on c."""
import math

FPS = 30
FLASH_OPACITY = [0.3, 0.9, 0.55, 0.25, 0.0]
FLASH_PUNCH = 1.045
FLASH_SETTLE = 8  # grid frames
HEADING_LAND = 0.12  # a heading's first line starts landing this long after its paper


def grid_frame(t: float) -> int:
    return math.ceil(t * FPS - 1e-9)


def punch_changes(states: list[list[float]], hidden: list[tuple[float, float]]) -> list[float]:
    """The start of every state that looks different from the one before it and isn't hidden: the cut's
    clock times of the visible camera changes. A time inside a hidden window (start up to, not including, end)
    is dropped. Times are the page's own (3 decimals), so the flash keys on the frame the picture changes on."""
    out = []
    for prev, cur in zip(states, states[1:]):
        if cur[1:] == prev[1:] or any(a <= cur[0] < b for a, b in hidden):
            continue
        out.append(round(cur[0], 3))  # the time the page itself sets the state at
    return out


def flash_keys(cuts: list[float]) -> dict:
    """The white's opacity and the punch's scale on each grid frame they touch. Two cuts closer than the
    flash is long: the later one wins every frame they share."""
    white: dict[int, float] = {}
    punch: dict[int, float] = {}
    for c in (grid_frame(t) for t in cuts):
        for k, opacity in enumerate(FLASH_OPACITY):
            white[c - 1 + k] = opacity
        for k in range(FLASH_SETTLE + 1):
            p = k / FLASH_SETTLE
            punch[c + k] = 1.0 if k == FLASH_SETTLE else round(FLASH_PUNCH + (1 - FLASH_PUNCH) * (1 - (1 - p) ** 2), 4)
    return {"white": sorted(white.items()), "punch": sorted(punch.items())}


def build_events(cuts: list[float], broll: list[dict], headings: list[dict], graphic_events: list[dict]) -> list[dict]:
    """Every sound event on the cut's clock, sorted by time. `cut` on each visible camera change (on the
    whitest frame) and each B-roll in and out; `hit` where each heading lands; then the graphics' own."""
    events = []
    for t in cuts:
        events.append({"role": "cut", "event": "camera change", "at": round(grid_frame(t) / FPS, 3), "sfx": True})
    for b in broll:
        events.append({"role": "cut", "event": "b-roll in", "at": round(b["start"], 3), "sfx": b["sfx"]})
        events.append({"role": "cut", "event": "b-roll out", "at": round(b["end"], 3), "sfx": b["sfx"]})
    for h in headings:
        events.append({"role": "hit", "event": "heading", "at": round(h["start"] + HEADING_LAND, 3), "sfx": h["sfx"]})
    return sorted(events + list(graphic_events), key=lambda e: e["at"])

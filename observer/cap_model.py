"""Failure cap: a bound on what a hacked server can say through which answers fail.

The server chooses which answers fail, so without a cap the pattern of failures can carry about
one bit per answer. With the cap, at most `failures` of the last `window` replies (not counting
capped ones) may be server-caused failures; one more trips a cooldown in which the next
`cooldown` replies are all unavailable, whatever the server does. A trip also clears the
history, so the window starts empty after the cooldown. The replies an outsider sees
then follow the automaton below, which observer/cap_bound.py counts to bound the channel.
This is the reference model: the gate (gate/core in Rust) must behave exactly like step().
"""
from __future__ import annotations

# Ages of the failures still in the window (0 = the newest reply), and replies of cooldown left.
State = tuple[tuple[int, ...], int]

START: State = ((), 0)

# Results the server can cause. "halted" and "capped" are the gate's own and don't count.
COUNTED = frozenset({"reject", "error", "deadline", "disconnect", "no_server"})


def check(window: int, failures: int, cooldown: int) -> None:
    if type(window) is not int or not 1 <= window <= 4096:
        raise ValueError("cap window must be an integer in [1, 4096]")
    if type(failures) is not int or not 0 <= failures < window:
        raise ValueError("cap failures must be an integer in [0, window)")
    # With no cooldown a trip only clears the window, so every answer could fail: no bound at all.
    if type(cooldown) is not int or not 1 <= cooldown <= 1 << 20:
        raise ValueError("cap cooldown must be an integer in [1, 2^20]")


def step(state: State, window: int, failures: int, cooldown: int, failed: bool) -> tuple[State, str]:
    """One reply, in the order replies leave. Returns the new state and "pass", "trip" or "capped".

    "pass" sends the reply as decided, "trip" sends it (a failure) and starts the cooldown, and
    "capped" replaces it with unavailable.
    """
    ages, left = state
    if left:
        return ((), left - 1), "capped"
    ages = tuple(a + 1 for a in ages if a + 1 < window)
    if not failed:
        return (ages, 0), "pass"
    if len(ages) >= failures:
        return ((), cooldown), "trip"
    return ((0,) + ages, 0), "pass"

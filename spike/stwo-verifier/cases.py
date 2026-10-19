"""Run the pinned verifier against good, foreign, weak and malformed proofs.

Usage: python3 -I cases.py VERIFIER FIXTURES_DIR WORK_DIR. Exits non-zero if any case gets the wrong exit code.
"""
import bz2
import os
import random
import subprocess
import sys

V, FIX, WORK = sys.argv[1:4]
PINS = dict(line.split() for line in open(os.path.join(FIX, "pins.txt")))
GOOD = os.path.join(FIX, "ret_opcode.bin")
good = open(GOOD, "rb").read()
raw = bz2.decompress(good)
failures = 0


def run(name, want, data=None, path=None, pin=PINS["ret_opcode"], extra=()):
    global failures
    if data is not None:
        path = os.path.join(WORK, name + ".bin")
        open(path, "wb").write(data)
    r = subprocess.run([V, path, pin, *extra], capture_output=True, text=True, timeout=120)
    detail = next((l for l in r.stderr.splitlines() if l.startswith(("[stwo]", "error:"))), "")
    ok = r.returncode in want
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}: rc={r.returncode} want {sorted(want)} {detail[:100]}")
    return r.returncode


run("valid", {0}, path=GOOD)
run("valid, empty output pinned", {0}, path=GOOD, extra=("",))
run("output pin differs", {1}, path=GOOD, extra=("0x1",))
run("proof of another program", {1}, path=GOOD, pin=PINS["all_opcode_components"])
run("weak config (2 bits)", {1}, path=os.path.join(FIX, "weak_all_opcode.bin"), pin=PINS["all_opcode_components"])
run("garbage", {2}, os.urandom(len(good)))
run("empty file", {2}, b"")
run("truncated", {2}, good[: len(good) // 2])
run("compressed byte flipped", {2}, good[:5000] + bytes([good[5000] ^ 1]) + good[5001:])
run("zeros after the stream", {2}, good + b"\x00" * 64)
run("second stream appended", {2}, good + bz2.compress(b"x" * 100))
run("trailing bytes in the payload", {2}, bz2.compress(raw + b"\x00" * 8, 9))
run("file over 8 MiB", {2}, os.urandom((8 << 20) + 1))
run("bzip2 bomb (1 GiB of zeros)", {2}, bz2.compress(b"\x00" * (1 << 30), 9))
run("huge length prefix", {2}, bz2.compress(b"\xff" * 64, 9))
random.seed(1)
for i in range(40):
    pos = random.randrange(len(raw))
    b = bytearray(raw)
    b[pos] ^= 1 << random.randrange(8)
    run(f"payload bit flip {i:02d} at {pos}", {1, 2}, bz2.compress(bytes(b), 9))
if failures:
    sys.exit(f"{failures} case(s) failed")
print("all cases passed")

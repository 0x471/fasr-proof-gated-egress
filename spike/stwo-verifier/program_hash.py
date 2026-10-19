"""Compute a Cairo program's hash from its compiled JSON, independently of the prover.

Mirrors stwo-cairo's get_verification_output: Blake2s-256 over the program words, each encoded
as 2 limbs (if below 2^63) or 8 limbs with the top bit set, limbs big-endian, bytes little-endian;
the digest read as a little-endian integer modulo the Stark prime.
Usage: python3 -I program_hash.py COMPILED.json
"""
import hashlib
import json
import sys

P = 2**251 + 17 * 2**192 + 1


def limbs(v):
    if v < 2**63:
        return [v >> 32, v & 0xFFFFFFFF]
    w = [(v >> (32 * i)) & 0xFFFFFFFF for i in range(8)]
    return [w[7] | 1 << 31] + w[6::-1]


def program_hash(data):
    h = hashlib.blake2s(digest_size=32)
    for word in data:
        v = int(word, 16) % P
        for limb in limbs(v):
            h.update(limb.to_bytes(4, "little"))
    return int.from_bytes(h.digest(), "little") % P


if __name__ == "__main__":
    prog = json.load(open(sys.argv[1]))
    print(hex(program_hash(prog["data"])))

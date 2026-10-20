"""The write-up has one home per fact; these tests fail when a copy drifts from it.

- Catalogue counts live in docs/ATTACKS.md (the summary must match its rows); other pages include it.
- Measured numbers live in book/src/results.md; a number quoted elsewhere must appear there.
- Constants live in the code; the pages that quote them must agree.
"""
import json
import random
import re

import pytest

from conftest import ROOT, TINY
from pge.cli import CLI_MEMORY, CLI_OPEN_FILES, MAX_OUTPUT_BYTES
from pge.protocol import PROOF_MAX, Wire
from pge.service import Policy

PLAIN = ["README.md"] + [f"book/src/{p}.md" for p in
                         ("introduction", "how-it-works", "leakage", "hardening", "limitations", "glossary", "tour")]
REFERENCE = ["docs/ATTACKS.md", "docs/THREAT_MODEL.md", "docs/SERVICE_SPEC.md", "docs/BINDING_SPEC.md"]
RESULTS = "book/src/results.md"


def text(path):
    return (ROOT / path).read_text()


def test_catalogue_summary_matches_its_rows():
    s = text("docs/ATTACKS.md")
    rows = re.findall(r"^\| (K\d+) \|", s, re.M)
    assert len(rows) == len(set(rows))
    m = re.search(r"has (\d+) scenarios: (\d+) stopped, (\d+) allowed on purpose, (\d+) that still work.*?, and (\d+) out "
                  r"of scope", s)
    total, *parts = map(int, m.groups())
    assert total == len(rows) == sum(parts)
    out_of_scope = s.split("## Out of scope", 1)[1]
    assert parts[3] == len(re.findall(r"^\| K\d+ \|", out_of_scope, re.M))
    table = dict(re.findall(r"^\| (Stopped|Allowed on purpose|Still works[^|]*|Out of scope) \| (\d+) \|", s, re.M))
    assert [int(v) for v in table.values()] == parts


def test_catalogue_counts_are_only_written_in_the_catalogue():
    for path in PLAIN + REFERENCE + [RESULTS]:
        if path != "docs/ATTACKS.md":
            assert not re.search(r"\b\d+ scenarios\b", text(path)), f"{path}: include docs/ATTACKS.md:summary instead"


# Numbers with units, p-values and counts. Formats differ between pages ("12 of 16" here, "12/16" there).
NUMBER = re.compile(r"(?<![\d.])\d[\d,]*(?:\.\d+)?(?:-\d[\d,]*(?:\.\d+)?)? ?(?:ms|s|%|MiB|MB|bit/s)\b"
                    r"|p = 0\.\d+|(?<![\d.])\d+/\d+\b|\b\d+ of \d+\b")


def _found(token, ref):
    if token in ref:
        return True
    if m := re.fullmatch(r"(\d+) of (\d+)", token):
        return f"{m[1]}/{m[2]}" in ref or re.search(rf"\b{m[1]} \w+ in {m[2]}\b", ref) is not None
    bare = re.sub(r"^p = | ?(ms|s|%|MiB|MB|bit/s)$", "", token)
    return re.search(rf"(?<![\d.]){re.escape(bare)}(?![\d.])", ref) is not None


@pytest.mark.parametrize("path", PLAIN + REFERENCE)
def test_quoted_measurements_come_from_the_measurements_page(path):
    ref = text(RESULTS)
    missing = sorted(t for t in set(NUMBER.findall(text(path))) if not _found(t, ref))
    assert not missing, f"{path} quotes numbers that book/src/results.md doesn't have: {missing}"


def _ints(pattern, paths):
    return [int(v.replace(",", "")) for p in paths for v in re.findall(pattern, text(p))]


def test_documented_constants_match_the_code():
    pages = PLAIN + REFERENCE + [RESULTS]
    wire = Wire(input_lens=(4,), output_lens=(3,))
    proof = len(json.loads((TINY / "proof.json").read_text())["proof"])
    checks = [
        (r"`max_pending` \(default (\d+)\)", Policy().max_pending),
        (r"(\d+) MiB of (?:memory|address space)|Address space[^|]*\| (\d+) MiB", CLI_MEMORY >> 20),
        (r"(\d+) open files|Open files \| (\d+)", CLI_OPEN_FILES),
        (r"Output \| (\d+) KiB|passes (\d+) KiB", MAX_OUTPUT_BYTES >> 10),
        (r"Question \|[^|]*\| (\d+) bytes", wire.request_size),
        (r"Reply \|[^|]*\| (\d+) bytes", wire.reply_size),
        (r"(\d+) bytes \+ proof", wire.answer_fixed),
        (r"up to (\d+) KiB", PROOF_MAX >> 10),
        (r"exactly ([\d,]+) bytes", proof),
    ]
    for pattern, value in checks:
        found = [int(next(g for g in m if g).replace(",", "")) if isinstance(m, tuple) else int(m.replace(",", ""))
                 for p in pages for m in re.findall(pattern, text(p))]
        assert found and set(found) == {value}, (pattern, found, value)


def test_documented_fuzz_count_matches_the_fuzz_test():
    from test_protocol import _candidates
    n = sum(1 for _ in _candidates(random.Random(0)))
    assert set(_ints(r"([\d,]+) random and mutated messages", PLAIN + REFERENCE)) == {n}


def test_tests_named_as_evidence_exist():
    defined = {n for f in (ROOT / "tests").glob("*.py") for n in re.findall(r"^def (test_\w+)", f.read_text(), re.M)}
    pages = PLAIN + REFERENCE + [RESULTS] + [str(p.relative_to(ROOT)) for p in (ROOT / "book/src").glob("*.md")]
    named = {(p, n) for p in set(pages) for n in re.findall(r"`(test_\w+)(?:\[[^\]]*\])?`", text(p))}
    assert not [x for x in named if x[1] not in defined]

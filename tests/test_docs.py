"""The write-up has one home per fact; these tests fail when a copy drifts from it.

- Catalogue counts live in docs/ATTACKS.md (the summary must match its rows); other pages include it.
- Measured numbers live in book/src/results.md; a number quoted elsewhere must appear there.
- Constants live in the code; the pages that quote them must agree.
"""
import json
import random
import re
import subprocess

import pytest

from conftest import ROOT, TINY
from pge.cli import CLI_MEMORY, CLI_OPEN_FILES, MAX_OUTPUT_BYTES
from pge.protocol import PROOF_MAX, Wire
from pge.service import Policy

PLAIN = ["README.md"] + [f"book/src/{p}.md" for p in
                         ("introduction", "leakage", "hardening", "limitations", "glossary", "tour")]
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


# Numbers with units, p-values and counts: measurements look like this.
NUMBER = re.compile(r"(?<![\d.])\d[\d,]*(?:\.\d+)?(?:-\d[\d,]*(?:\.\d+)?)? ?(?:ms|s|%|MiB|KiB|MB|bit/s)\b"
                    r"|p [=<] 0\.\d+|(?<![\d.])\d+/\d+\b|\b\d+ of \d+\b")
# Settings, code constants and a pre-registered decision threshold, not measurements; the
# constants are checked against the code below.
ALLOWED = {"4 s", "5 ms", "1 s", "5 s", "64 KiB", "256 MiB", "1 MiB", "p < 0.01"}
README_COPY = re.compile(r"<!-- BEGIN findings[^>]*-->\n(.*?)<!-- END findings -->\n", re.S)


def _anchor(name):
    return re.search(rf"<!-- ANCHOR: {name} -->\n(.*?)<!-- ANCHOR_END: {name} -->\n", text(RESULTS), re.S).group(1)


def _documents():
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    skip = {RESULTS, "RULES.md"}
    return [p for p in out.split() if p not in skip and not p.startswith(("docs/results/", "spike/"))]


def test_measurements_are_written_only_in_results():
    for path in _documents():
        body = README_COPY.sub("", text(path))
        found = sorted(set(NUMBER.findall(body)) - ALLOWED)
        assert not found, f"{path} writes measurements {found}; include them from {RESULTS} or link to it"


def test_readme_findings_are_an_exact_copy():
    assert README_COPY.search(text("README.md")).group(1) == _anchor("findings")


def test_allowed_constants_match_the_code():
    from pge.service import SERVER_BUFFER_MAX
    from pge.verdict import MAX_PROOF_BYTES
    assert PROOF_MAX == MAX_OUTPUT_BYTES == 64 << 10 and CLI_MEMORY == 256 << 20
    assert SERVER_BUFFER_MAX == MAX_PROOF_BYTES == 1 << 20


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


def test_the_spin_verdict_is_included_not_copied():
    # The verdict lives in results.md (anchor spin-verdict); book pages include it.
    for path in PLAIN:
        assert "0.041" not in text(path), f"{path}: use {{{{#include results.md:spin-verdict}}}}"

# Repository map

## The gate (`pge/`)

| File | What it does |
|---|---|
| `pge/service.py` | The gate service: one approved model, at most one answer per question, three release rules (arrival order by default, immediate as in v0, fixed time); replies are the proven output or one fixed error; logs use fixed words only |
| `pge/protocol.py` | The message format: fixed-size binary messages for the approved model, parsed strictly |
| `pge/binding.py` | Encodes the question exactly as EZKL does, checks the server's output values, decodes proven outputs |
| `pge/bundle.py` | Loads the model's files only if each matches its pinned hash |
| `pge/domain.py` | The range of questions the test model can answer, so impossible ones are refused up front |
| `pge/verdict.py` | Turns a proof check into ACCEPT, REJECT or ERROR |
| `pge/cli.py` | Checks proofs with EZKL's official command-line tool, under resource limits |
| `pge/proc.py` | Runs a program with a deadline, an output cap and resource limits |
| `pge/worker.py` | Runs one check in a separate process with a deadline (Python version) |

## Documents (`docs/`)

| File | What it covers |
|---|---|
| `docs/THREAT_MODEL.md` | Assets, attackers, trust boundaries, assumptions, attack surface, side channels and their status, the MITRE ATLAS mapping |
| `docs/ATTACKS.md` | Every attack tried, its result and the test that covers it |
| `docs/SERVICE_SPEC.md` | The gate service's exact behaviour |
| `docs/BINDING_SPEC.md` | The request-binding rules, pitfalls and test vectors |

## Tests and experiments

| Path | What it is |
|---|---|
| `tests/` | Unit tests, comparisons against EZKL's own encoding, end-to-end checks, a parser fuzz test, service tests including the leakage encoders |
| `tests/fixtures/` | Two pinned test models: `tiny` (4 inputs, 3 outputs, with its input range in `domain.json`) and `multi` (two inputs, two outputs) |
| `prover/server.py` | The laptop AI server: honest, or misbehaving in one of several ways, including hiding bits in timing, order or failures |
| `observer/client.py` | The outside observer: sends questions, records each answer and its time, decodes hidden bits |
| `observer/analyze_matrix.py` | Summarises a release-rule experiment |
| `observer/jitter_test.py` | Tests whether leftover timing differences follow the server's hidden bits |
| `observer/order_experiment.py` | The ordering experiment on one machine |

## Running on the Pi (`device/`)

| File | What it does |
|---|---|
| `device/verify_on_pi.sh`, `device/check_verifier.sh` | Copy the pinned model to the Pi and check accept and reject cases with the real tool, timed |
| `device/pi_scenario.sh` | One experiment: the gate on the Pi, the server and observer on this machine |
| `device/pi_matrix.sh` | Every release rule against every signalling method |
| `device/verify_cost.py` | What one check costs on the Pi, and how reliable each memory limit is |
| `device/measure.sh` | Snapshot of the device's attack surface: packages, services, processes, open ports, file hashes |

## Other

| Path | What it is |
|---|---|
| `image/` | Builds the stripped-down v1 system with Buildroot (work in progress) |
| `book/` | This book (mdBook); the reference pages include the files in `docs/` |
| `spike/rust-verifier/` | Side experiment: EZKL's checker as a small Rust program |
| `spike/stwo-verifier/` | Side experiment: a pinned checker for S-two (StarkWare) proofs |

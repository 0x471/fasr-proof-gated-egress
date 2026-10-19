# Limitations and open questions

> **In short**
> - Not built yet: the stripped v1 system running the gate, secure boot, signed updates, and a defence for the failure channel.
> - Not measured yet: a real upper bound on leakage, several users at once, packet-level signals, the speed-versus-leakage trade-off.
> - The results assume all of the server's traffic goes through the gate, one approved model, and a sound proof system.

## Not built yet

- **The v1 image.** The v1 software runs on the stock OS. The stripped image (only the gate, two wired ports, read-only system, a separate unprivileged user for the verifier, a log partition) is in progress.
- **v2:** signed, versioned updates with a rollback rule and power-loss-safe installation; secure boot (which permanently writes a key into the board, so it needs a spare board); a hash-chained log sent to a separate collector.
- **A defence for the failure channel.** No release policy limits it. Capping failures per window bounds what the failure pattern alone can carry: if the observer sees only which of N answers failed and the gate allows at most F, the server can send at most log2(C(N,0) + ... + C(N,F)) bits per window; stopping at the first failure leaves N + 1 outcomes per window, if restarts are controlled. Timing, logs and restarts need their own accounting, and none of this is implemented or measured yet.

## Not measured yet

- **A capacity estimate.** The numbers are achieved rates for planted bits. An estimator validated on synthetic channels with known capacity comes next, before claiming any bound.
- **Several clients at once,** other loads, and a packet capture on the outside link (packet sizes and TCP behaviour are still the stock OS's).
- **The `busy` channel:** a server that holds requests open makes other clients' requests fail; it depends on load and is not measured.
- **The budget trade-off:** fixed release was run with one budget (4 s); leakage and lost answers against the budget are not measured.
- **The residual timing trace** under fixed release: its mechanism, and whether the redesigned spin (waking early and waiting precisely) removes it. Spin removed the average shift in one 128-answer run but probably not all of the signal, and that run used an earlier version of the spin code; it needs repeating with more answers, finer timestamps and several clients.

## Assumptions the results rest on

- Every path from the server to the outside goes through the device. On the v0 OS this is not enforced.
- One pinned model and circuit; the binding rules are specific to EZKL 23.0.5.
- The verifier binary, the bundle and the interpreter are trusted inputs of the image. The gate checks the bundle's hashes at load and before every check; the CLI's download is checked by its digest when it is fetched; the interpreter and the CLI on disk are not re-checked at run time, so they rely on the read-only image.
- The proof system is sound. Its soundness is out of scope.

## Out of scope

Physical attacks and physical side channels (power, electromagnetic, acoustic, thermal), what ordinary correct outputs reveal about a model (extraction from many answers), and the soundness of the proof system itself.

## Open questions

1. After a failed check, should the device keep answering (and count failures) or stop until reset? Halting is a denial-of-service lever and still reveals where the halting failure was.
2. Which replay property matters: never delivering an answer twice, or proving each answer was freshly computed?
3. Should answers be ordered across all clients (a silent request then delays everyone) or per client (the server can then order answers across clients)?
4. Who may read the device's logs? The log still carries the number and timing of events.
5. How much latency and how many lost answers are acceptable in exchange for less leakage?

# Glossary

**Arrival order.** A release rule: each answer leaves only after the answers to every earlier question. Stops the server from choosing the order.

**Bit.** The smallest unit of information: one yes/no. 16 bits is enough for two letters of text. Leak rates here are in bits per answer or per second.

**Bundle.** The approved model's files the gate needs to check proofs: its settings, its verification key and the setup file (SRS). The gate pins each by its hash.

**Channel.** Any way a hacked server can get information to someone outside: timing, order, failures, packet sizes, the gate's logs.

**Deadline / budget.** How long the gate waits for a valid answer before replying `unavailable`. With fixed-time release, every answer also leaves no earlier than its deadline.

**EZKL.** The open-source tool used here to prove the outputs of a small machine-learning model and to check those proofs.

**Fixed-time release.** A release rule: answers leave in arrival order, each never earlier than a set time after its question, whatever the server did. It promises "never early", not a latest time; how late answers actually left is in Measurements. It hides most of the server's timing at the cost of speed; a small trace was measured without spin.

**Gate.** The Raspberry Pi running the checking software, between the AI server and the outside world.

**Hash / pin.** A short fingerprint of a file. "Pinned by hash" means the gate refuses to run if the file's fingerprint differs from the approved one.

**Proof (zero-knowledge proof).** A short piece of data showing that a specific computation produced a specific output, which anyone can check quickly without redoing the computation.

**Release rule (release policy).** When the gate sends an answer back: immediately, in arrival order, or at a fixed time.

**Replay.** Sending an old valid answer again. The gate matches each answer to exactly one pending question, so no question gets two answers. An old proof for the same question can still answer a new request; for one deterministic model that gives the server no choice of output, but it isn't proof of fresh computation.

**Request binding.** Making sure a proof is about **this** question, **this** model and **this** reading of the output, not just "some valid computation".

**Residual.** What still leaks after a defence. Here: the sub-millisecond timing trace left by fixed-time release.

**Side channel.** A channel that isn't the content itself: when, in what order or whether something happens.

**SRS.** The public setup file the proof system needs. Pinned by hash like the rest of the bundle.

**`unavailable`.** The gate's single error reply for anything involving the server. It never says why, so the server can't use the error text to signal.

**v0, v1, v2.** The gate's versions: v0 on the standard Pi system; v1 with stricter software (including arrival-order and fixed-time release) on a stripped-down system; v2 adds secure boot, signed updates, fixed time slots and a hash-chained log.

**Verification key (vk).** The approved model's public key for checking its proofs.

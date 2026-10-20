# How the gate works

> **In short**
> - The gate checks each answer's proof against the question **it** received, not against anything the server says about the question.
> - It sends back either the proven answer or the fixed error `unavailable`, never anything the server wrote. (A well-formed question the model can't answer gets `bad_request` before it reaches the server; a malformed message closes the connection.)
> - It can release answers in three ways; the choice decides how much a hacked server can signal through timing and order.

## The life of one question

1. **A user sends a question** to the gate. A malformed message closes the connection. A well-formed question outside what the model can answer gets `bad_request` straight away. Either way nothing reaches the server.
2. **The gate forwards the question** to the AI server, tagged with the gate's own counter. The server doesn't get the client's id or connection, though it still sees the question itself and when it arrived.
3. **The server answers** with an output and a proof that the approved model computed that output from that question.
4. **The gate checks the proof.** It rebuilds what the proof must be about from its own copy of the question, uses only the approved model's pinned key, and runs the check with strict limits on time and memory.
5. **The gate replies.** If the proof checks out, the user gets the output, decoded from the proven values. Otherwise the user gets `unavailable`. Every reply has the same size.

Each question gets exactly one decision and at most one reply. Late, duplicate or unexpected answers from the server are dropped.

## Why the gate encodes the question itself

A proof only says "this model turned some input into some output". The gate has to make sure the input is **this** question, the model is **the approved one**, and the output is read the right way. Leaving any of these to the server or to default settings breaks the guarantee. Three examples we found:

- Changing one number in the model's settings file left the proof valid but turned the answer 0.97 into 7918.6. So the gate pins every model file by its hash.
- The proof tool accepts a valid proof with extra bytes on the end, which a server could use to smuggle data. So the gate fixes the exact proof length.
- Python rounds numbers differently from the proof tool, so a naive gate would encode some questions differently from the proof. The gate copies the tool's rounding exactly and is tested against it.

The full rules are in the [Request binding specification](binding-spec.md).

## Three ways to release answers

| Release rule | When an answer leaves | What it stops | The cost |
|---|---|---|---|
| Immediate (v0) | As soon as it is checked | Nothing | None |
| Arrival order (v1, the default) | After every earlier question's answer | Reordering (shown on one connection) | A slow answer holds up the ones behind it |
| Fixed time | In arrival order, and never before a set time after its question (on the Pi, one client at a time with a 5 ms spin, answers reached the observer 0.96-3.84 ms after it) | Reordering, and the timing codes we tried (a small trace remained without spin) | Every answer waits the full time; under load answers can leave later |

No release rule can stop the server from making chosen answers fail, because the server can always send a bad proof. That is the open problem described in [What a hacked server can still leak](leakage.md).

## The proof system

The gate uses [EZKL](https://github.com/zkonduit/ezkl) 23.0.5, an open-source tool that proves the output of small machine-learning models. The test model is tiny (4 inputs, 3 outputs) and deterministic, because the research question is about the gate, not the prover. A production proof system would take the same place in the design, but it needs its own binding rules (what exactly the proof states, how inputs and outputs are encoded, any randomness) and its own review.

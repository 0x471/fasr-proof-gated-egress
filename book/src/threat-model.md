# Threat model

> **In short**
> - **What we protect:** the model's weights on the AI server, and everything that decides what gets out: the pinned model files on the endpoint, the endpoint's code and settings, when answers are released, and the endpoint's own logs.
> - **Who attacks:** mainly the AI server itself (hacked, or a model acting against its owners) working with an outside accomplice who reads the signals; also anyone on the network, and later anyone holding a signing key.
> - **Key assumption:** every path from the server to the outside goes through the endpoint. Physical attacks on the device are out of scope.
> - Section 6 lists every way a server can signal around a valid proof and how far each is closed; section 8 maps the threats to MITRE ATLAS.

This is the full reference document. For a plain overview, read [What a hacked server can still leak](leakage.md) and [How hard the endpoint is to attack](hardening.md) first.

{{#include ../../docs/THREAT_MODEL.md:2:}}

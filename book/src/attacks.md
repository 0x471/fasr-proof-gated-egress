# Attack catalogue

> **In short**
> - Every attack tried so far, by us or by reviewers, with its result and the test that now covers it.
> - 32 attacks: 22 stopped, 2 allowed on purpose, 6 still work or are only partly stopped, 2 out of scope.
> - The ones that still work are all about signalling (timing, failures, keeping the gate busy) or about the standard Pi system, which v1 replaces.

How to read a row: **Version** is the gate version the attack was tried against; **Level** says how it was shown (on the real Pi, in automated tests, or only proposed); **Result** says what happened; the last column names the test.

{{#include ../../docs/ATTACKS.md:2:}}

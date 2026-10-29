# Attack catalogue

> **In short**
> - Every attack tried so far, by us or by reviewers, with its result and the test that now covers it.
> - 34 scenarios: 23 stopped, 2 allowed on purpose, 7 still work, are only partly stopped or are untested, 2 out of scope.
> - The ones that still work are all about signalling (timing, failures, keeping the endpoint busy) or about the standard Pi system, which v1 replaces.

How to read a row: **Version** is the endpoint version the attack was tried against; **Level** says how it was shown (on the real Pi, in automated tests, or only proposed); **Result** says what happened; the last column names the test.

{{#include ../../docs/ATTACKS.md:2:}}

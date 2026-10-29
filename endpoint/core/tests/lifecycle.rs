use pge_core::*;

fn core(mode: Mode, halt: Option<u64>) -> Core {
    Core::new(Policy::new(100, 32, halt, mode, 5).unwrap())
}
fn accept(c: &mut Core, seq: u64, now: u64) {
    assert_eq!(c.answer(seq), Answer::Check);
    c.complete(seq, now, Decision::accept());
}
fn drain(c: &mut Core, now: u64) -> Vec<(u64, Decision)> {
    let mut out = Vec::new();
    while let Step::Release(s, d) = c.pump(now, true) {
        out.push((s, d));
    }
    out
}

#[test]
fn arrival_orders_success_and_failure() {
    let mut c = core(Mode::Arrival, None);
    for i in 0..3 {
        c.admit(i, true);
    }
    accept(&mut c, 3, 5);
    c.answer(2);
    c.complete(2, 5, Decision::failure("reject", "proof"));
    assert_eq!(c.pump(5, false), Step::Idle);
    accept(&mut c, 1, 6);
    assert_eq!(
        drain(&mut c, 6)
            .iter()
            .map(|(s, d)| (*s, d.ok()))
            .collect::<Vec<_>>(),
        [(1, true), (2, false), (3, true)]
    );
}

#[test]
fn late_checks_and_duplicates_cannot_reply_twice() {
    let mut c = core(Mode::Arrival, None);
    c.admit(0, true);
    assert_eq!(c.answer(987), Answer::Stale);
    assert_eq!(c.answer(1), Answer::Check);
    assert_eq!(c.answer(1), Answer::Duplicate);
    c.complete(1, 100, Decision::accept());
    assert_eq!(drain(&mut c, 100), [(1, Decision::failure("deadline", ""))]);
    c.complete(1, 101, Decision::accept());
    assert_eq!(c.answer(1), Answer::Stale);
    assert!(drain(&mut c, 102).is_empty());
}

#[test]
fn held_success_expires_only_in_arrival_mode() {
    for mode in [Mode::Arrival, Mode::Fixed] {
        let mut c = core(mode, None);
        c.admit(0, true);
        c.admit(1, true);
        accept(&mut c, 2, 5);
        c.expire(200);
        let got = drain(&mut c, 200);
        assert!(!got[0].1.ok());
        assert_eq!(got[1].1.ok(), mode == Mode::Fixed);
        assert_eq!(c.failures(), if mode == Mode::Fixed { 1 } else { 2 });
    }
}

#[test]
fn earlier_deadline_unblocks_later_timely_success() {
    let mut c = core(Mode::Arrival, None);
    c.admit(0, true);
    c.admit(20, true);
    accept(&mut c, 2, 30);
    c.expire(100);
    assert_eq!(
        drain(&mut c, 100)
            .iter()
            .map(|(_, d)| d.ok())
            .collect::<Vec<_>>(),
        [false, true]
    );
}

#[test]
fn halt_revokes_held_successes_and_all_pending() {
    for mode in [Mode::Arrival, Mode::Fixed] {
        for bad in [1, 3] {
            let mut c = core(mode, Some(1));
            for i in 0..3 {
                c.admit(i, true);
            }
            accept(&mut c, 2, 3);
            c.answer(bad);
            c.complete(bad, 4, Decision::failure("reject", "proof_length"));
            assert!(c.halted());
            assert_eq!(c.take_halt_notice(), Some(1));
            assert_eq!(c.admit(4, true), Admission::Halted);
            let got = drain(&mut c, 200);
            assert_eq!(got.iter().map(|(s, _)| *s).collect::<Vec<_>>(), [1, 2, 3]);
            assert!(got.iter().all(|(_, d)| !d.ok()));
        }
    }
}

#[test]
fn failures_accumulate_across_successes() {
    let mut c = core(Mode::Arrival, Some(2));
    c.admit(0, false);
    drain(&mut c, 0);
    c.admit(1, true);
    accept(&mut c, 2, 2);
    drain(&mut c, 2);
    assert!(!c.halted());
    c.admit(3, false);
    assert!(c.halted());
    assert_eq!(c.failures(), 2);
}

#[test]
fn held_answers_use_capacity_in_both_modes() {
    for mode in [Mode::Arrival, Mode::Fixed] {
        let mut c = Core::new(Policy::new(100, 2, None, mode, 5).unwrap());
        c.admit(0, true);
        c.admit(0, true);
        accept(&mut c, 2, 1);
        assert_eq!(c.admit(1, true), Admission::Busy);
        assert_eq!(c.len(), 2);
    }
}

#[test]
fn disconnect_and_halt_never_request_a_spin_inline() {
    for halt in [None, Some(1)] {
        let mut c = core(Mode::Fixed, halt);
        for i in 0..20 {
            c.admit(i * 2, true);
        }
        c.disconnect("closed");
        assert_eq!(c.len(), 20);
        assert_eq!(c.pump(97, false), Step::SpinUntil(100));
        assert!(matches!(c.pump(100, true), Step::Release(1, _)));
        assert_eq!(c.pump(100, true), Step::WaitUntil(100));
        assert!(matches!(c.pump(100, false), Step::SpinUntil(102)));
        assert_eq!(drain(&mut c, 200).len(), 19);
    }
}

#[test]
fn expiry_during_spin_does_not_allow_overtaking() {
    let mut c = core(Mode::Fixed, None);
    c.admit(0, true);
    c.admit(2, true);
    accept(&mut c, 2, 3);
    assert_eq!(c.pump(99, false), Step::Idle);
    c.expire(100);
    assert!(matches!(c.pump(100, false), Step::Release(1, _)));
    assert_eq!(c.pump(100, false), Step::SpinUntil(102));
    assert_eq!(c.pump(101, true), Step::WaitUntil(101));
    assert!(matches!(c.pump(102, true), Step::Release(2, _)));
}

#[test]
fn no_server_consumes_and_releases_its_seq() {
    for mode in [Mode::Fixed, Mode::Arrival] {
        let mut c = core(mode, None);
        assert_eq!(c.admit(0, false), Admission::Admitted(1));
        c.admit(1, true);
        accept(&mut c, 2, 2);
        assert_eq!(
            drain(&mut c, 200)
                .iter()
                .map(|(s, _)| *s)
                .collect::<Vec<_>>(),
            [1, 2]
        );
    }
}

#[test]
fn expired_checks_do_not_start_and_budget_shrinks() {
    let mut c = core(Mode::Arrival, None);
    c.admit(0, true);
    c.answer(1);
    assert_eq!(c.budget(1, 10), Some(90));
    assert_eq!(c.budget(1, 99), Some(1));
    assert_eq!(c.budget(1, 100), None);
    assert_eq!(c.budget(1, 101), None);
}

#[test]
fn policy_and_counter_arithmetic_are_checked() {
    for (d, n, h, s) in [
        (0, 1, None, 0),
        (1, 0, None, 0),
        (1, 4097, None, 0),
        (1, 1, Some(0), 0),
        (1, 1, None, 50_000_001),
    ] {
        assert!(Policy::new(d, n, h, Mode::Fixed, s).is_err());
    }
    assert_eq!(
        core(Mode::Fixed, None).admit(u64::MAX, true),
        Admission::Exhausted
    );
}

#[test]
fn random_event_sequences_preserve_safety_and_liveness() {
    for seed in 1..=200u64 {
        for mode in [Mode::Arrival, Mode::Fixed] {
            let mut c = core(mode, if seed % 3 == 0 { Some(7) } else { None });
            let (mut rng, mut now, mut admitted, mut released) = (seed, 0, Vec::new(), Vec::new());
            for _ in 0..1000 {
                rng ^= rng << 13;
                rng ^= rng >> 7;
                rng ^= rng << 17;
                now += rng % 3;
                let seq = 1 + (rng >> 8) % 200;
                match rng % 6 {
                    0 | 1 => {
                        if let Admission::Admitted(s) = c.admit(now, rng % 17 != 0) {
                            admitted.push((s, now));
                        }
                    }
                    2 => {
                        c.answer(seq);
                    }
                    3 => c.complete(
                        seq,
                        now,
                        if rng % 19 == 0 {
                            Decision::failure("error", "verifier")
                        } else {
                            Decision::accept()
                        },
                    ),
                    4 => c.expire(now),
                    _ => {
                        if rng % 29 == 0 {
                            c.disconnect("closed");
                        }
                    }
                }
                for (s, _) in drain(&mut c, now) {
                    let (expected, start) = admitted[released.len()];
                    assert_eq!(s, expected);
                    if mode == Mode::Fixed {
                        assert!(now >= start + 100);
                    }
                    released.push(s);
                }
                assert!(c.len() <= 32);
            }
            now += 200;
            c.expire(now);
            released.extend(drain(&mut c, now).into_iter().map(|(s, _)| s));
            assert_eq!(
                released,
                admitted.iter().map(|(s, _)| *s).collect::<Vec<_>>()
            );
            assert!(c.is_empty());
        }
    }
}

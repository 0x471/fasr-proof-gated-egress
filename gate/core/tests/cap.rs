use pge_core::{Admission, Answer, Cap, Core, Decision, Mode, Policy, Step};

// Feeds one decided answer at a time through the core and returns what was released.
fn run(cap: Cap, decisions: &[Decision]) -> Vec<(&'static str, bool)> {
    let mut core = Core::new(
        Policy::new(100, 8, None, Mode::Arrival, 0)
            .unwrap()
            .with_cap(cap),
    );
    let mut out = Vec::new();
    for (i, d) in decisions.iter().enumerate() {
        let now = i as u64;
        let Admission::Admitted(seq) = core.admit(now, true) else {
            panic!("not admitted");
        };
        assert_eq!(core.answer(seq), Answer::Check);
        core.complete(seq, now, *d);
        let Step::Release(s, released) = core.pump(now, true) else {
            panic!("not released");
        };
        assert_eq!(s, seq);
        out.push((released.result, core.take_cap_trip()));
    }
    out
}

fn reject() -> Decision {
    Decision::failure("reject", "proof")
}

#[test]
fn trips_on_one_failure_too_many_then_cools_down() {
    let cap = Cap::new(4, 1, 2).unwrap();
    let got = run(cap, &[reject(); 6]);
    assert_eq!(
        got,
        [
            ("reject", false),
            ("reject", true),
            ("capped", false),
            ("capped", false),
            ("reject", false),
            ("reject", true),
        ]
    );
}

#[test]
fn accepted_answers_are_suppressed_during_the_cooldown() {
    let cap = Cap::new(4, 0, 2).unwrap();
    let ok = Decision::accept();
    let got = run(cap, &[reject(), ok, ok, ok]);
    assert_eq!(
        got,
        [
            ("reject", true),
            ("capped", false),
            ("capped", false),
            ("accept", false),
        ]
    );
}

#[test]
fn failures_further_apart_than_the_window_never_trip() {
    let cap = Cap::new(3, 1, 5).unwrap();
    let ok = Decision::accept();
    let got = run(cap, &[reject(), ok, ok, reject(), ok, ok, reject()]);
    assert!(got.iter().all(|(r, trip)| *r != "capped" && !trip));
    let got = run(cap, &[reject(), ok, reject()]);
    assert_eq!(got[2], ("reject", true));
}

#[test]
fn halted_answers_do_not_count_towards_the_cap() {
    let cap = Cap::new(4, 0, 1).unwrap();
    let got = run(cap, &[Decision::failure("halted", ""), Decision::accept()]);
    assert_eq!(got, [("halted", false), ("accept", false)]);
}

#[test]
fn caps_without_a_cooldown_or_with_too_many_failures_are_refused() {
    assert!(Cap::new(4, 1, 0).is_err());
    assert!(Cap::new(4, 4, 1).is_err());
    assert!(Cap::new(0, 0, 1).is_err());
    assert!(Cap::new(4097, 0, 1).is_err());
    assert!(Cap::new(4, 3, 1 << 20).is_ok());
}

fn released(core: &mut Core, now: u64) -> Vec<(u64, &'static str, bool)> {
    let mut out = Vec::new();
    while let Step::Release(seq, d) = core.pump(now, true) {
        out.push((seq, d.result, core.take_cap_trip()));
    }
    out
}

#[test]
fn every_server_caused_failure_counts() {
    let cap = Cap::new(1, 0, 1).unwrap();
    for reason in ["reject", "error", "deadline", "disconnect"] {
        let got = run(cap, &[Decision::failure(reason, ""), Decision::accept()]);
        assert_eq!(got, [(reason, true), ("capped", false)], "{reason}");
    }
}

#[test]
fn no_server_counts_towards_the_cap() {
    let policy = Policy::new(100, 8, None, Mode::Arrival, 0).unwrap();
    let mut core = Core::new(policy.with_cap(Cap::new(1, 0, 1).unwrap()));
    assert_eq!(core.admit(0, false), Admission::Admitted(1));
    assert_eq!(released(&mut core, 0), [(1, "no_server", true)]);
    let Admission::Admitted(seq) = core.admit(1, true) else {
        panic!("not admitted");
    };
    assert_eq!(core.answer(seq), Answer::Check);
    core.complete(seq, 1, Decision::accept());
    assert_eq!(released(&mut core, 1), [(2, "capped", false)]);
}

#[test]
fn late_arrival_answers_are_failures_before_the_cap_sees_them() {
    // A timely success held behind a request that never got an answer leaves after its own
    // deadline, so arrival release turns it into a failure; the cap must count that failure.
    let policy = Policy::new(100, 8, None, Mode::Arrival, 0).unwrap();
    let mut core = Core::new(policy.with_cap(Cap::new(4, 1, 1).unwrap()));
    assert_eq!(core.admit(0, true), Admission::Admitted(1));
    assert_eq!(core.admit(1, true), Admission::Admitted(2));
    assert_eq!(core.answer(2), Answer::Check);
    core.complete(2, 10, Decision::accept());
    core.expire(150);
    assert_eq!(
        released(&mut core, 150),
        [(1, "deadline", false), (2, "deadline", true)]
    );
    assert_eq!(core.admit(160, true), Admission::Admitted(3));
    assert_eq!(core.answer(3), Answer::Check);
    core.complete(3, 161, Decision::accept());
    assert_eq!(released(&mut core, 161), [(3, "capped", false)]);
}

#[test]
fn the_cap_works_the_same_under_fixed_release() {
    let policy = Policy::new(100, 8, None, Mode::Fixed, 0).unwrap();
    let mut core = Core::new(policy.with_cap(Cap::new(1, 0, 1).unwrap()));
    assert_eq!(core.admit(0, true), Admission::Admitted(1));
    assert_eq!(core.answer(1), Answer::Check);
    core.complete(1, 1, reject());
    assert!(released(&mut core, 99).is_empty());
    assert_eq!(released(&mut core, 100), [(1, "reject", true)]);
    assert_eq!(core.admit(100, true), Admission::Admitted(2));
    assert_eq!(core.answer(2), Answer::Check);
    core.complete(2, 101, Decision::accept());
    assert!(released(&mut core, 199).is_empty());
    assert_eq!(released(&mut core, 200), [(2, "capped", false)]);
}

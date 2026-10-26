use pge_core::{Admission, Answer, Cap, Core, Decision, Mode, Policy, Step};

// Feeds one decided answer at a time through the core and returns what was released.
fn run(cap: Cap, decisions: &[Decision]) -> Vec<(&'static str, bool)> {
    let mut core = Core::new(Policy::new(100, 8, None, Mode::Arrival, 0).unwrap().with_cap(cap));
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

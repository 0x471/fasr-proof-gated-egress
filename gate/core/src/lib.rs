#![forbid(unsafe_code)]

use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Arrival,
    Fixed,
}

// Failure cap, the same automaton as observer/cap_model.py: at most `failures` server-caused
// failures among the last `window` replies that weren't capped; one more makes the next
// `cooldown` replies unavailable, whatever the server does.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Cap {
    pub window: u32,
    pub failures: u32,
    pub cooldown: u32,
}

impl Cap {
    pub fn new(window: u32, failures: u32, cooldown: u32) -> Result<Self, &'static str> {
        // With no cooldown a trip only clears the window, so the cap would bound nothing.
        if !(1..=4096).contains(&window) || failures >= window || !(1..=1 << 20).contains(&cooldown)
        {
            return Err("invalid cap");
        }
        Ok(Self {
            window,
            failures,
            cooldown,
        })
    }
}

// Results the server can cause; "halted" and "capped" are the gate's own.
const COUNTED: [&str; 5] = ["reject", "error", "deadline", "disconnect", "no_server"];

#[derive(Clone, Copy, Debug)]
pub struct Policy {
    pub deadline: u64,
    pub capacity: usize,
    pub halt_after: Option<u64>,
    pub mode: Mode,
    pub spin: u64,
    pub cap: Option<Cap>,
}

impl Policy {
    pub fn new(
        deadline: u64,
        capacity: usize,
        halt_after: Option<u64>,
        mode: Mode,
        spin: u64,
    ) -> Result<Self, &'static str> {
        if deadline == 0
            || deadline > 3_600_000_000_000
            || !(1..=4096).contains(&capacity)
            || halt_after.is_some_and(|n| n == 0 || n > 1 << 30)
            || spin > 50_000_000
        {
            return Err("invalid policy");
        }
        Ok(Self {
            deadline,
            capacity,
            halt_after,
            mode,
            spin,
            cap: None,
        })
    }
    pub fn with_cap(self, cap: Cap) -> Self {
        Self {
            cap: Some(cap),
            ..self
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Decision {
    pub result: &'static str,
    pub reason: &'static str,
}

impl Decision {
    pub fn accept() -> Self {
        Self {
            result: "accept",
            reason: "",
        }
    }
    pub fn failure(result: &'static str, reason: &'static str) -> Self {
        Self { result, reason }
    }
    pub fn ok(self) -> bool {
        self.result == "accept"
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Admission {
    Admitted(u64),
    Busy,
    Halted,
    Exhausted,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Answer {
    Check,
    Duplicate,
    Stale,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Step {
    Release(u64, Decision),
    WaitUntil(u64),
    SpinUntil(u64),
    Idle,
}
#[derive(Clone, Copy, Debug)]
enum State {
    Waiting,
    Checking,
    Held(Decision),
}
#[derive(Clone, Copy, Debug)]
struct Entry {
    due: u64,
    state: State,
}

pub struct Core {
    policy: Policy,
    seq: u64,
    entries: BTreeMap<u64, Entry>,
    failures: u64,
    halted: bool,
    halt_notice: Option<u64>,
    cap_ages: Vec<u32>,
    cap_left: u32,
    cap_trip: bool,
}

impl Core {
    pub fn new(policy: Policy) -> Self {
        Self {
            policy,
            seq: 0,
            entries: BTreeMap::new(),
            failures: 0,
            halted: false,
            halt_notice: None,
            cap_ages: Vec::new(),
            cap_left: 0,
            cap_trip: false,
        }
    }
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn halted(&self) -> bool {
        self.halted
    }
    pub fn failures(&self) -> u64 {
        self.failures
    }
    pub fn take_halt_notice(&mut self) -> Option<u64> {
        self.halt_notice.take()
    }
    // True once after the release that tripped the failure cap.
    pub fn take_cap_trip(&mut self) -> bool {
        std::mem::take(&mut self.cap_trip)
    }

    fn apply_cap(&mut self, decision: Decision) -> Decision {
        let Some(cap) = self.policy.cap else {
            return decision;
        };
        if self.cap_left > 0 {
            self.cap_left -= 1;
            return Decision::failure("capped", "");
        }
        self.cap_ages.retain_mut(|a| {
            *a += 1;
            *a < cap.window
        });
        if !COUNTED.contains(&decision.result) {
            return decision;
        }
        if self.cap_ages.len() >= cap.failures as usize {
            self.cap_ages.clear();
            self.cap_left = cap.cooldown;
            self.cap_trip = true;
        } else {
            self.cap_ages.push(0);
        }
        decision
    }

    pub fn admit(&mut self, now: u64, server: bool) -> Admission {
        if self.halted {
            return Admission::Halted;
        }
        if self.entries.len() >= self.policy.capacity {
            return Admission::Busy;
        }
        let Some(seq) = self.seq.checked_add(1).filter(|n| *n < 1 << 53) else {
            return Admission::Exhausted;
        };
        let Some(due) = now.checked_add(self.policy.deadline) else {
            return Admission::Exhausted;
        };
        self.seq = seq;
        self.entries.insert(
            seq,
            Entry {
                due,
                state: State::Waiting,
            },
        );
        if !server {
            self.decide(seq, Decision::failure("no_server", ""));
        }
        Admission::Admitted(seq)
    }

    pub fn answer(&mut self, seq: u64) -> Answer {
        match self.entries.get_mut(&seq) {
            Some(Entry {
                state: state @ State::Waiting,
                ..
            }) => {
                *state = State::Checking;
                Answer::Check
            }
            Some(Entry {
                state: State::Checking,
                ..
            }) => Answer::Duplicate,
            _ => Answer::Stale,
        }
    }

    pub fn budget(&mut self, seq: u64, now: u64) -> Option<u64> {
        let p = self.entries.get(&seq)?;
        if !matches!(p.state, State::Checking) {
            return None;
        }
        if now >= p.due {
            self.decide(seq, Decision::failure("deadline", ""));
            return None;
        }
        Some(p.due - now)
    }

    pub fn complete(&mut self, seq: u64, now: u64, decision: Decision) {
        if self.budget(seq, now).is_some() {
            self.decide(seq, decision);
        }
    }

    fn decide(&mut self, seq: u64, decision: Decision) {
        let Some(p) = self.entries.get_mut(&seq) else {
            return;
        };
        if matches!(p.state, State::Held(_)) {
            return;
        }
        p.state = State::Held(decision);
        if !decision.ok() {
            self.count_failure();
        }
    }

    fn count_failure(&mut self) {
        self.failures = self.failures.saturating_add(1);
        if !self.halted && self.policy.halt_after.is_some_and(|n| self.failures >= n) {
            self.halted = true;
            self.halt_notice = Some(self.failures);
            for p in self.entries.values_mut() {
                if !matches!(p.state, State::Held(_)) {
                    p.state = State::Held(Decision::failure("halted", ""));
                }
            }
        }
    }

    pub fn disconnect(&mut self, reason: &'static str) {
        let seqs: Vec<_> = self
            .entries
            .iter()
            .filter(|(_, p)| !matches!(p.state, State::Held(_)))
            .map(|(s, _)| *s)
            .collect();
        for seq in seqs {
            self.decide(seq, Decision::failure("disconnect", reason));
        }
    }

    pub fn expire(&mut self, now: u64) {
        let seqs: Vec<_> = self
            .entries
            .iter()
            .filter(|(_, p)| p.due <= now && !matches!(p.state, State::Held(_)))
            .map(|(s, _)| *s)
            .collect();
        for seq in seqs {
            self.decide(seq, Decision::failure("deadline", ""));
        }
    }

    pub fn next_expiry(&self) -> Option<u64> {
        self.entries
            .values()
            .filter(|p| !matches!(p.state, State::Held(_)))
            .map(|p| p.due)
            .min()
    }

    // A SpinUntil action is only a request to the driver. Re-enter with fresh time,
    // and spun=true, after at most one bounded wait in that driver step.
    pub fn pump(&mut self, now: u64, spun: bool) -> Step {
        let Some((&seq, p)) = self.entries.first_key_value() else {
            return Step::Idle;
        };
        let State::Held(mut decision) = p.state else {
            return Step::Idle;
        };
        if self.policy.mode == Mode::Fixed && now < p.due {
            return if !spun && p.due - now <= self.policy.spin {
                Step::SpinUntil(p.due)
            } else {
                Step::WaitUntil(p.due.saturating_sub(self.policy.spin).max(now))
            };
        }
        let late =
            decision.ok() && !self.halted && self.policy.mode == Mode::Arrival && now >= p.due;
        if decision.ok() && (self.halted || late) {
            decision = if late {
                Decision::failure("deadline", "held")
            } else {
                Decision::failure("halted", "")
            };
        }
        self.entries.remove(&seq);
        if late {
            self.count_failure();
        }
        Step::Release(seq, self.apply_cap(decision))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_never_early_and_admission_order() {
        let mut core = Core::new(Policy::new(100, 8, None, Mode::Fixed, 5).unwrap());
        assert_eq!(core.admit(0, true), Admission::Admitted(1));
        assert_eq!(core.admit(2, true), Admission::Admitted(2));
        assert_eq!(core.answer(2), Answer::Check);
        core.complete(2, 10, Decision::accept());
        assert_eq!(core.pump(99, false), Step::Idle);
        core.expire(100);
        assert_eq!(
            core.pump(100, false),
            Step::Release(1, Decision::failure("deadline", ""))
        );
        assert_eq!(core.pump(100, false), Step::SpinUntil(102));
        assert_eq!(core.pump(102, true), Step::Release(2, Decision::accept()));
        assert_eq!(core.pump(200, false), Step::Idle);
    }
}

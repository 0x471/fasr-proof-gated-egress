use crate::{
    config::Config,
    network::{Connection, Input},
};
use pge_core::{Admission, Answer, Core, Decision, Mode, Step};
use pge_verify::{read_regular, Bundle, Domain, Pins, Verdict};
use pge_wire::{Message, Reply, Wire};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, VecDeque},
    fs::OpenOptions,
    io::{self, Read, Write},
    net::{TcpListener, TcpStream},
    os::{fd::AsRawFd, unix::net::UnixStream},
    sync::{mpsc, Arc},
    thread,
    time::{Duration, Instant},
};

// Upper bound on one poll wait: a lost wake-up delays the loop by at most this long.
const MAX_WAIT_MS: i32 = 1000;

struct Client {
    conn: Connection,
    last: Option<u64>,
    outstanding: usize,
    reading: bool,
    read_since: Instant,
    drain_until: Option<Instant>,
}
struct Request {
    client: u64,
    cid: u64,
    x: Vec<f64>,
    hash: String,
    start: u64,
    y: Option<Vec<f64>>,
}
struct Job {
    seq: u64,
    data: Vec<u8>,
}
struct Endpoint {
    config: Config,
    bundle: Arc<Bundle>,
    domain: Option<Domain>,
    wire: Wire,
    core: Core,
    clock: Instant,
    log: Box<dyn Write>,
    log_errors: u64,
    clients: BTreeMap<u64, Client>,
    requests: BTreeMap<u64, Request>,
    jobs: VecDeque<Job>,
    server: Option<Connection>,
    accept_paused: Option<Instant>,
    listener_fd: i32,
}
impl Endpoint {
    fn now(&self) -> u64 {
        u64::try_from(self.clock.elapsed().as_nanos()).unwrap_or(u64::MAX)
    }
    fn log(&mut self, value: Value) {
        if serde_json::to_writer(&mut self.log, &value)
            .and_then(|_| self.log.write_all(b"\n").map_err(serde_json::Error::io))
            .is_err()
            || self.log.flush().is_err()
        {
            self.log_errors = self.log_errors.saturating_add(1);
        }
    }
    fn notice(&mut self) {
        if let Some(n) = self.core.take_halt_notice() {
            self.log(json!({"result":"halted","failures":n}));
        }
    }
    fn send(&mut self, client: u64, cid: u64, reply: Reply) {
        let Ok(payload) = self.wire.encode(&Message::Reply(cid, reply)) else {
            return;
        };
        if let Some(c) = self.clients.get_mut(&client) {
            c.outstanding = c.outstanding.saturating_sub(1);
            if c.conn.send(&payload).is_err() {
                self.clients.remove(&client);
            }
        }
    }
    // Returns the clock time to wake for the next fixed release, if one is waiting.
    fn pump(&mut self, spin_allowed: bool) -> Option<u64> {
        self.notice();
        let mut spun = !spin_allowed;
        loop {
            match self.core.pump(self.now(), spun) {
                Step::Release(seq, decision) => {
                    if self.core.take_cap_trip() {
                        self.log(json!({"result":"cap_tripped"}));
                    }
                    if let Some(p) = self.requests.remove(&seq) {
                        let reply = if decision.ok() {
                            p.y.map(Reply::Ok).unwrap_or(Reply::Unavailable)
                        } else {
                            Reply::Unavailable
                        };
                        self.send(p.client, p.cid, reply);
                        let ms = ((self.now().saturating_sub(p.start)) as f64 / 100_000.)
                            .round_ties_even()
                            / 10.;
                        let mut record =
                            json!({"result":decision.result,"seq":seq,"x_sha256":p.hash,"ms":ms});
                        if !decision.reason.is_empty() {
                            record["reason"] = json!(decision.reason);
                        }
                        self.log(record);
                    }
                    self.notice();
                }
                Step::SpinUntil(due) => {
                    let start = Instant::now();
                    while self.now() < due
                        && start.elapsed() < Duration::from_nanos(self.config.policy.spin)
                    {
                        std::hint::spin_loop();
                    }
                    spun = true;
                }
                Step::WaitUntil(t) => return Some(t),
                Step::Idle => return None,
            }
        }
    }
    fn changed(&mut self) {
        self.notice();
        if self.config.policy.mode == Mode::Arrival {
            let _ = self.pump(false);
        }
    }
    fn submit(&mut self, client: u64, cid: u64, x: Vec<f64>) {
        let n = self.bundle.spec.quantize(&x);
        let valid = n.as_ref().is_ok_and(|n| {
            self.domain
                .as_ref()
                .is_none_or(|d| self.bundle.spec.input_lens.len() == 1 && d.admits(n))
        });
        if !valid {
            self.send(client, cid, Reply::BadRequest);
            self.log(json!({"result":"bad_request","reason":"request"}));
            return;
        }
        let Ok(hash) = n.and_then(|n| self.bundle.spec.request_hash(&n)) else {
            self.send(client, cid, Reply::BadRequest);
            self.log(json!({"result":"bad_request","reason":"request"}));
            return;
        };
        let start = self.now();
        match self.core.admit(start, self.server.is_some()) {
            Admission::Admitted(seq) => {
                let msg = self.wire.encode(&Message::Forward(seq, x.clone()));
                self.requests.insert(
                    seq,
                    Request {
                        client,
                        cid,
                        x,
                        hash,
                        start,
                        y: None,
                    },
                );
                if let Some(s) = self.server.as_mut() {
                    let failed = match msg {
                        Ok(ref m) => s.send(m).is_err(),
                        Err(_) => true,
                    };
                    if failed {
                        self.disconnect("lost");
                    } else if s.buffered > 1 << 20 {
                        self.log(json!({"result":"server_stalled"}));
                        self.disconnect("closed");
                    }
                }
                self.changed();
            }
            other => {
                self.send(client, cid, Reply::Unavailable);
                self.log(json!({"result":if other == Admission::Halted {"halted"} else {"busy"},"x_sha256":hash}));
            }
        }
    }
    fn answer(&mut self, seq: u64, outputs: Vec<[u8; 32]>, proof: Vec<u8>) {
        match self.core.answer(seq) {
            Answer::Stale => self.log(json!({"result":"stale_answer"})),
            Answer::Duplicate => self.log(json!({"result":"duplicate_answer","seq":seq})),
            Answer::Check => {
                let Some(p) = self.requests.get_mut(&seq) else {
                    return;
                };
                match self
                    .bundle
                    .spec
                    .bind(&p.x, &outputs, &proof, self.config.proof_bytes)
                {
                    Ok((data, y)) => {
                        p.y = Some(y);
                        self.jobs.push_back(Job { seq, data });
                    }
                    Err(e) => {
                        let reason = if e == "proof_length" {
                            "proof_length"
                        } else {
                            "binding"
                        };
                        self.core
                            .complete(seq, self.now(), Decision::failure("reject", reason));
                        self.changed();
                    }
                }
            }
        }
    }
    fn disconnect(&mut self, reason: &'static str) {
        self.server = None;
        self.core.disconnect(reason);
        self.changed();
    }
    fn read_server(&mut self) {
        for _ in 0..32 {
            let Some(s) = self.server.as_mut() else {
                break;
            };
            if s.flush().is_err() {
                self.disconnect("lost");
                break;
            }
            let expired = s
                .started
                .is_some_and(|t| t.elapsed() >= Duration::from_nanos(self.config.policy.deadline));
            let input = if expired {
                Input::Invalid
            } else {
                s.read(self.wire.answer_max)
            };
            match input {
                Input::Frame(raw) => match self.wire.parse(3, &raw) {
                    Ok(Message::Answer(seq, outputs, proof)) => self.answer(seq, outputs, proof),
                    _ => {
                        self.log(json!({"result":"server_violation"}));
                        self.disconnect("violation");
                        break;
                    }
                },
                Input::Invalid => {
                    self.log(json!({"result":"server_violation"}));
                    self.disconnect("violation");
                    break;
                }
                Input::Closed => {
                    self.disconnect("closed");
                    break;
                }
                Input::Lost => {
                    self.disconnect("lost");
                    break;
                }
                Input::Pending => break,
            }
        }
    }
    fn read_clients(&mut self) {
        let ids: Vec<_> = self.clients.keys().copied().collect();
        for id in ids {
            for _ in 0..32 {
                let Some(c) = self.clients.get_mut(&id) else {
                    break;
                };
                if c.conn.flush().is_err() {
                    self.clients.remove(&id);
                    break;
                }
                if !c.reading {
                    if (c.outstanding == 0 && c.conn.buffered == 0)
                        || c.drain_until.is_some_and(|t| Instant::now() >= t)
                    {
                        self.clients.remove(&id);
                    }
                    break;
                }
                // The idle check comes first: a client that sends but never reads would otherwise
                // keep an expired idle timer, and poll would get a zero timeout forever.
                if c.read_since.elapsed() >= self.config.idle {
                    c.reading = false;
                    c.drain_until = Instant::now().checked_add(
                        Duration::from_nanos(self.config.policy.deadline) + Duration::from_secs(1),
                    );
                    self.log(json!({"result":"client_idle"}));
                    break;
                }
                if c.conn.buffered > 65536 {
                    break;
                }
                match c.conn.read(self.wire.request_size) {
                    Input::Frame(raw) => {
                        let parsed = self.wire.parse(1, &raw);
                        let reason = match parsed {
                            Ok(Message::Request(cid, x)) => {
                                if c.last.is_some_and(|last| cid <= last) {
                                    Some("ids")
                                } else {
                                    c.last = Some(cid);
                                    c.read_since = Instant::now();
                                    c.outstanding += 1;
                                    self.submit(id, cid, x);
                                    None
                                }
                            }
                            _ => Some("protocol"),
                        };
                        if let Some(reason) = reason {
                            self.clients.remove(&id);
                            self.log(json!({"result":"client_violation","reason":reason}));
                            break;
                        }
                    }
                    Input::Closed => {
                        c.reading = false;
                        c.drain_until = Instant::now().checked_add(
                            Duration::from_nanos(self.config.policy.deadline)
                                + Duration::from_secs(1),
                        );
                        break;
                    }
                    Input::Invalid => {
                        self.clients.remove(&id);
                        self.log(json!({"result":"client_violation","reason":"protocol"}));
                        break;
                    }
                    Input::Lost => {
                        self.clients.remove(&id);
                        break;
                    }
                    Input::Pending => break,
                }
            }
        }
    }
}

pub fn run(config: Config) -> Result<(), String> {
    let pins = read_regular(&config.pins, 65536).map_err(str::to_owned)?;
    let pins = Pins::parse(std::str::from_utf8(&pins).map_err(|_| "pins encoding")?)
        .map_err(str::to_owned)?;
    let bundle = Arc::new(Bundle::load(&config.bundle, pins).map_err(str::to_owned)?);
    let domain = config
        .domain
        .as_ref()
        .map(|p| read_regular(p, 1 << 20).and_then(|b| Domain::parse(&b)))
        .transpose()
        .map_err(str::to_owned)?;
    let wire = bundle.spec.wire().map_err(str::to_owned)?;
    let log: Box<dyn Write> = match &config.log {
        Some(p) => Box::new(
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(p)
                .map_err(|_| "log open")?,
        ),
        None => Box::new(io::stderr()),
    };
    let listener = TcpListener::bind(config.listen).map_err(|e| format!("listen: {e}"))?;
    listener.set_nonblocking(true).map_err(|_| "listener")?;
    let listener_fd = listener.as_raw_fd();
    let mut endpoint = Endpoint {
        core: Core::new(config.policy),
        config,
        bundle,
        domain,
        wire,
        clock: Instant::now(),
        log,
        log_errors: 0,
        clients: BTreeMap::new(),
        requests: BTreeMap::new(),
        jobs: VecDeque::new(),
        server: None,
        accept_paused: None,
        listener_fd,
    };
    // Threads write one byte here when they finish, so the poll below wakes for them.
    // One shared writer: cloning the socket per thread would need a descriptor each time,
    // and running out of descriptors must not stop the endpoint.
    let (mut wake_rx, wake_tx) = UnixStream::pair().map_err(|_| "wake pipe")?;
    wake_rx.set_nonblocking(true).map_err(|_| "wake pipe")?;
    wake_tx.set_nonblocking(true).map_err(|_| "wake pipe")?;
    let wake_tx = Arc::new(wake_tx);
    let (done_tx, done_rx) = mpsc::channel();
    let (mut active, mut client_seq) = (false, 0u64);
    let mut connecting: Option<mpsc::Receiver<io::Result<TcpStream>>> = None;
    let mut retry = Instant::now();
    let mut delay = Duration::from_millis(500);
    let mut was_connected = false;
    let mut release_at: Option<u64> = None;
    loop {
        if was_connected && endpoint.server.is_none() {
            retry = Instant::now() + delay;
            delay = (delay * 2).min(Duration::from_secs(10));
        }
        was_connected = endpoint.server.is_some();
        if endpoint.server.is_none() && connecting.is_none() && Instant::now() >= retry {
            let (tx, rx) = mpsc::channel();
            let addr = endpoint.config.server;
            let wake = Arc::clone(&wake_tx);
            let spawned = thread::Builder::new().spawn(move || {
                let _ = tx.send(TcpStream::connect_timeout(&addr, Duration::from_secs(10)));
                let _ = (&*wake).write(&[1]);
            });
            if spawned.is_ok() {
                connecting = Some(rx);
            } else {
                endpoint.log(json!({"result":"connect_failed"}));
                retry = Instant::now() + delay;
                delay = (delay * 2).min(Duration::from_secs(10));
            }
        }

        let wait = next_wait(&endpoint, release_at, connecting.is_none().then_some(retry));
        poll_once(&endpoint, &wake_rx, wait);
        let mut sink = [0u8; 64];
        while matches!(wake_rx.read(&mut sink), Ok(n) if n > 0) {}

        if let Some(rx) = &connecting {
            if let Ok(result) = rx.try_recv() {
                connecting = None;
                match result.and_then(Connection::new) {
                    Ok(c) => {
                        endpoint.server = Some(c);
                        was_connected = true;
                        delay = Duration::from_millis(500);
                    }
                    Err(_) => {
                        endpoint.log(json!({"result":"connect_failed"}));
                        retry = Instant::now() + delay;
                        delay = (delay * 2).min(Duration::from_secs(10));
                    }
                }
            }
        }
        if endpoint.accept_paused.is_some_and(|t| Instant::now() >= t) {
            endpoint.accept_paused = None;
        }
        if endpoint.accept_paused.is_none() {
            accept_clients(&mut endpoint, &listener, &mut client_seq);
        }
        endpoint.read_server();
        endpoint.read_clients();
        endpoint.core.expire(endpoint.now());
        endpoint.changed();
        while let Ok((seq, verdict)) = done_rx.try_recv() {
            active = false;
            let decision = match verdict {
                Verdict::Accept => Decision::accept(),
                Verdict::Reject => Decision::failure("reject", "proof"),
                Verdict::Error => Decision::failure("error", "verifier"),
            };
            endpoint.core.complete(seq, endpoint.now(), decision);
            endpoint.changed();
        }
        if !active {
            while let Some(job) = endpoint.jobs.pop_front() {
                if let Some(left) = endpoint.core.budget(job.seq, endpoint.now()) {
                    let bundle = Arc::clone(&endpoint.bundle);
                    let tx = done_tx.clone();
                    let wake = Arc::clone(&wake_tx);
                    let len = endpoint.config.proof_bytes;
                    let seq = job.seq;
                    let started = thread::Builder::new().spawn(move || {
                        let verdict =
                            crate::verify_job(bundle, job.data, Duration::from_nanos(left), len);
                        let _ = tx.send((job.seq, verdict));
                        let _ = (&*wake).write(&[1]);
                    });
                    if started.is_ok() {
                        active = true;
                    } else {
                        endpoint.core.complete(
                            seq,
                            endpoint.now(),
                            Decision::failure("error", "verifier_failed"),
                        );
                    }
                    break;
                }
            }
            endpoint.changed();
        }
        // Exactly one spin opportunity per driver iteration, never from fail_all.
        release_at = endpoint.pump(true);
    }
}

fn accept_clients(endpoint: &mut Endpoint, listener: &TcpListener, client_seq: &mut u64) {
    for _ in 0..16 {
        match listener.accept() {
            Ok((stream, _)) => {
                if endpoint.clients.len() >= 64 {
                    endpoint.log(json!({"result":"client_refused"}));
                    continue;
                }
                let Ok(conn) = Connection::new(stream) else {
                    endpoint.log(json!({"result":"accept_failed"}));
                    continue;
                };
                *client_seq = client_seq.wrapping_add(1);
                endpoint.clients.insert(
                    *client_seq,
                    Client {
                        conn,
                        last: None,
                        outstanding: 0,
                        reading: true,
                        read_since: Instant::now(),
                        drain_until: None,
                    },
                );
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
            Err(e)
                if matches!(
                    e.kind(),
                    io::ErrorKind::Interrupted
                        | io::ErrorKind::ConnectionAborted
                        | io::ErrorKind::ConnectionReset
                ) =>
            {
                continue
            }
            // Out of descriptors or memory: keep serving, and stop accepting for a moment
            // so a full listen queue doesn't spin the loop (asyncio pauses the same way).
            Err(_) => {
                endpoint.log(json!({"result":"accept_failed"}));
                endpoint.accept_paused = Some(Instant::now() + Duration::from_secs(1));
                break;
            }
        }
    }
}

// Milliseconds poll may sleep: until the earliest timer, rounded up so it never wakes early.
fn next_wait(endpoint: &Endpoint, release_at: Option<u64>, retry: Option<Instant>) -> i32 {
    let now = Instant::now();
    let at = |ns: u64| endpoint.clock.checked_add(Duration::from_nanos(ns));
    let deadline = Duration::from_nanos(endpoint.config.policy.deadline);
    let mut times: Vec<Instant> = Vec::new();
    times.extend(release_at.and_then(at));
    times.extend(endpoint.core.next_expiry().and_then(at));
    if endpoint.server.is_none() {
        times.extend(retry);
    }
    if let Some(s) = &endpoint.server {
        times.extend(s.started.and_then(|t| t.checked_add(deadline)));
    }
    for c in endpoint.clients.values() {
        if c.reading {
            times.extend(c.read_since.checked_add(endpoint.config.idle));
        }
        times.extend(c.drain_until);
    }
    times.extend(endpoint.accept_paused);
    match times.into_iter().min() {
        None => MAX_WAIT_MS,
        Some(t) if t <= now => 0,
        Some(t) => {
            let ms = (t - now).as_nanos().div_ceil(1_000_000);
            i32::try_from(ms).unwrap_or(MAX_WAIT_MS).min(MAX_WAIT_MS)
        }
    }
}

fn poll_once(endpoint: &Endpoint, wake: &UnixStream, wait_ms: i32) {
    let fd = |fd, events| libc::pollfd {
        fd,
        events,
        revents: 0,
    };
    let mut fds = vec![fd(wake.as_raw_fd(), libc::POLLIN)];
    if endpoint.accept_paused.is_none() {
        fds.push(fd(endpoint.listener_fd, libc::POLLIN));
    }
    if let Some(s) = &endpoint.server {
        let out = if s.wants_write() { libc::POLLOUT } else { 0 };
        fds.push(fd(s.fd(), libc::POLLIN | out));
    }
    for c in endpoint.clients.values() {
        let input = if c.reading && c.conn.buffered <= 65536 {
            libc::POLLIN
        } else {
            0
        };
        let out = if c.conn.wants_write() {
            libc::POLLOUT
        } else {
            0
        };
        if input | out != 0 {
            fds.push(fd(c.conn.fd(), input | out));
        }
    }
    // An interruption just ends this wait; the loop re-checks everything after it. Any other
    // poll error (e.g. ENOMEM) sleeps instead, so a failing poll can't spin the loop.
    let ret = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, wait_ms) };
    if ret < 0 && io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
        thread::sleep(Duration::from_millis(wait_ms.clamp(1, 10) as u64));
    }
}

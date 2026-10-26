use pge_verify::Verdict;
use std::{
    io::{self, Read, Write},
    os::{fd::AsRawFd, unix::process::CommandExt},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

pub const OUTPUT_CAP: usize = 65536;

pub fn classify(code: Option<i32>, out: &[u8], err: &[u8]) -> Verdict {
    match (code, out, err) {
        (Some(0), b"accept\n", b"") => Verdict::Accept,
        (Some(1), b"reject\n", b"") => Verdict::Reject,
        _ => Verdict::Error,
    }
}

fn nonblocking(fd: i32) -> io::Result<()> {
    // The fd is borrowed from a live pipe; fcntl does not take ownership.
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags < 0 || libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}
fn kill_group(child: &mut Child) {
    // The leader is not reaped until after this call, so its PID cannot be reused.
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let _ = child.kill();
    let _ = child.wait();
}
fn read_chunk(pipe: &mut impl Read, out: &mut Vec<u8>, eof: &mut bool) -> io::Result<()> {
    if *eof {
        return Ok(());
    }
    let mut buf = [0; 8192];
    match pipe.read(&mut buf) {
        Ok(0) => *eof = true,
        Ok(n) => out.extend_from_slice(&buf[..n]),
        Err(e)
            if matches!(
                e.kind(),
                io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
            ) =>
        {
            ()
        }
        Err(e) => return Err(e),
    }
    Ok(())
}

pub fn run(mut cmd: Command, timeout: Duration, input: &[u8]) -> Verdict {
    if timeout.is_zero() {
        return Verdict::Error;
    }
    let end = match Instant::now().checked_add(timeout) {
        Some(v) => v,
        None => return Verdict::Error,
    };
    cmd.env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("LC_ALL", "C")
        .env("RAYON_NUM_THREADS", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(_) => return Verdict::Error,
    };
    let (Some(mut stdout), Some(mut stderr)) = (child.stdout.take(), child.stderr.take()) else {
        kill_group(&mut child);
        return Verdict::Error;
    };
    let mut stdin = child.stdin.take();
    if nonblocking(stdout.as_raw_fd()).is_err()
        || nonblocking(stderr.as_raw_fd()).is_err()
        || stdin
            .as_ref()
            .is_none_or(|p| nonblocking(p.as_raw_fd()).is_err())
    {
        kill_group(&mut child);
        return Verdict::Error;
    }
    let mut offset = 0;
    let (mut out, mut err, mut oe, mut ee) = (Vec::new(), Vec::new(), false, false);
    loop {
        if offset == input.len() {
            stdin.take();
        }
        if let Some(pipe) = stdin.as_mut() {
            match pipe.write(&input[offset..]) {
                Ok(0) => {
                    kill_group(&mut child);
                    return Verdict::Error;
                }
                Ok(n) => offset += n,
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                    ) =>
                {
                    ()
                }
                Err(_) => {
                    kill_group(&mut child);
                    return Verdict::Error;
                }
            }
        }
        if Instant::now() >= end
            || read_chunk(&mut stdout, &mut out, &mut oe).is_err()
            || read_chunk(&mut stderr, &mut err, &mut ee).is_err()
            || out.len().saturating_add(err.len()) > OUTPUT_CAP
        {
            kill_group(&mut child);
            return Verdict::Error;
        }
        if oe && ee {
            match child.try_wait() {
                Ok(Some(status)) => {
                    return if offset == input.len() && Instant::now() < end {
                        classify(status.code(), &out, &err)
                    } else {
                        Verdict::Error
                    }
                }
                Ok(None) => (),
                Err(_) => {
                    kill_group(&mut child);
                    return Verdict::Error;
                }
            }
        }
        thread::sleep(Duration::from_millis(1).min(end.saturating_duration_since(Instant::now())));
    }
}

pub fn limits(cpu: u64) -> io::Result<()> {
    if cpu == 0 || cpu > 3600 {
        return Err(io::Error::other("cpu limit"));
    }
    // This runs in a fresh executable before starting any threads or reading hostile data.
    unsafe {
        for (resource, value) in [
            (libc::RLIMIT_CORE, 0),
            (libc::RLIMIT_CPU, cpu),
            (libc::RLIMIT_FSIZE, 0),
            (libc::RLIMIT_NOFILE, 64),
        ] {
            // Only ever lower a limit, soft or hard: never more than the gate itself was given.
            let mut old = libc::rlimit {
                rlim_cur: 0,
                rlim_max: 0,
            };
            if libc::getrlimit(resource, &mut old) != 0 {
                return Err(io::Error::last_os_error());
            }
            let value = (value as libc::rlim_t).min(old.rlim_cur).min(old.rlim_max);
            let limit = libc::rlimit {
                rlim_cur: value,
                rlim_max: value,
            };
            if libc::setrlimit(resource, &limit) != 0 {
                return Err(io::Error::last_os_error());
            }
        }
        #[cfg(target_os = "linux")]
        {
            let mut old = libc::rlimit {
                rlim_cur: 0,
                rlim_max: 0,
            };
            if libc::getrlimit(libc::RLIMIT_AS, &mut old) != 0 {
                return Err(io::Error::last_os_error());
            }
            let value = ((256 << 20) as libc::rlim_t)
                .min(old.rlim_cur)
                .min(old.rlim_max);
            let limit = libc::rlimit {
                rlim_cur: value,
                rlim_max: value,
            };
            if libc::setrlimit(libc::RLIMIT_AS, &limit) != 0 {
                return Err(io::Error::last_os_error());
            }
        }
        for sig in [libc::SIGPIPE, libc::SIGXFSZ] {
            if libc::signal(sig, libc::SIG_DFL) == libc::SIG_ERR {
                return Err(io::Error::last_os_error());
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn shell(script: &str, ms: u64) -> Verdict {
        let mut cmd = Command::new("/bin/sh");
        cmd.args(["-c", script]);
        run(cmd, Duration::from_millis(ms), b"")
    }
    #[test]
    fn successful_exit_and_one_marker_are_both_required() {
        assert_eq!(shell("printf 'accept\\n'", 1000), Verdict::Accept);
        assert_eq!(shell("printf 'reject\\n'; exit 1", 1000), Verdict::Reject);
        for script in [
            "printf 'accept\\n'; exit 101",
            "printf 'accept\\n'; kill -KILL $$",
            "printf 'reject\\n'; kill -KILL $$",
            "printf 'accept\\naccept\\n'",
            "printf 'accept\\n'; echo bad >&2",
            "exit 0",
        ] {
            assert_eq!(shell(script, 1000), Verdict::Error, "{script}");
        }
    }
    #[test]
    fn killed_child_cannot_accept() {
        assert_eq!(
            shell("printf 'accept\\n'; kill -KILL $$", 1000),
            Verdict::Error
        );
    }
    #[test]
    fn deadline_kills_grandchildren_too() {
        let path = std::env::temp_dir().join(format!("pge-grandchild-{}", std::process::id()));
        let mut cmd = Command::new("/bin/sh");
        cmd.args(["-c", "sleep 30 & printf '%s' $! > \"$1\"; wait", "probe"])
            .arg(&path);
        assert_eq!(run(cmd, Duration::from_secs(1), b""), Verdict::Error);
        let pid = std::fs::read_to_string(&path).unwrap();
        std::fs::remove_file(path).unwrap();
        let mut stopped = false;
        for _ in 0..100 {
            let status = Command::new("/bin/ps")
                .args(["-o", "stat=", "-p", pid.trim()])
                .output()
                .unwrap();
            let state = String::from_utf8_lossy(&status.stdout);
            if state.trim().is_empty() || state.trim().starts_with('Z') {
                stopped = true;
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        if !stopped {
            let _ = Command::new("/bin/kill")
                .args(["-KILL", pid.trim()])
                .status();
        }
        assert!(stopped, "grandchild survived the process-group deadline");
    }
    #[test]
    fn hung_and_flooding_children_are_errors() {
        for script in [
            "printf 'accept\\n'; sleep 30",
            "exec 1>&-; exec 2>&-; sleep 30",
            "while :; do echo xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx; done",
        ] {
            let start = Instant::now();
            assert_eq!(shell(script, 100), Verdict::Error);
            assert!(start.elapsed() < Duration::from_secs(2));
        }
    }
    #[test]
    fn unexpected_exit_is_never_rejection() {
        for code in [None, Some(2), Some(101), Some(127)] {
            assert_eq!(classify(code, b"reject\n", b""), Verdict::Error);
            assert_eq!(classify(code, b"accept\n", b""), Verdict::Error);
        }
    }
}

use std::{
    collections::VecDeque,
    io::{self, Read, Write},
    net::TcpStream,
    os::fd::{AsRawFd, RawFd},
    time::Instant,
};

pub enum Input {
    Frame(Vec<u8>),
    Pending,
    Closed,
    Invalid,
    Lost,
}

pub struct Connection {
    stream: TcpStream,
    input: Vec<u8>,
    size: Option<usize>,
    pub started: Option<Instant>,
    output: VecDeque<Vec<u8>>,
    written: usize,
    pub buffered: usize,
}
impl Connection {
    pub fn new(stream: TcpStream) -> io::Result<Self> {
        stream.set_nonblocking(true)?;
        stream.set_nodelay(true)?;
        Ok(Self {
            stream,
            input: Vec::new(),
            size: None,
            started: None,
            output: VecDeque::new(),
            written: 0,
            buffered: 0,
        })
    }
    pub fn fd(&self) -> RawFd {
        self.stream.as_raw_fd()
    }
    pub fn wants_write(&self) -> bool {
        !self.output.is_empty()
    }
    pub fn read(&mut self, limit: usize) -> Input {
        loop {
            if self.size.is_none() && self.input.len() == 4 {
                let Ok(head) = self.input.as_slice().try_into() else {
                    return Input::Invalid;
                };
                match pge_wire::frame_len(head, limit) {
                    Ok(n) => {
                        self.size = Some(n);
                        self.input.clear();
                    }
                    Err(_) => return Input::Invalid,
                }
            }
            if self.size == Some(self.input.len()) {
                self.size = None;
                self.started = None;
                return Input::Frame(std::mem::take(&mut self.input));
            }
            let need = self.size.unwrap_or(4).saturating_sub(self.input.len());
            let mut buf = [0; 8192];
            let n = need.min(buf.len());
            match self.stream.read(&mut buf[..n]) {
                Ok(0) => {
                    return if self.input.is_empty() && self.size.is_none() {
                        Input::Closed
                    } else {
                        Input::Invalid
                    }
                }
                Ok(n) => {
                    if self.started.is_none() {
                        self.started = Some(Instant::now());
                    }
                    self.input.extend_from_slice(&buf[..n]);
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Input::Pending,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => return Input::Pending,
                Err(_) => return Input::Lost,
            }
        }
    }
    pub fn send(&mut self, payload: &[u8]) -> io::Result<()> {
        let data = pge_wire::frame(payload).map_err(|_| io::Error::other("frame"))?;
        self.buffered = self
            .buffered
            .checked_add(data.len())
            .ok_or_else(|| io::Error::other("output size"))?;
        self.output.push_back(data);
        self.flush()
    }
    pub fn flush(&mut self) -> io::Result<()> {
        while let Some(front) = self.output.front() {
            match self.stream.write(&front[self.written..]) {
                Ok(0) => return Err(io::Error::other("closed")),
                Ok(n) => {
                    self.written += n;
                    self.buffered -= n;
                    if self.written == front.len() {
                        self.output.pop_front();
                        self.written = 0;
                    }
                }
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                    ) =>
                {
                    return Ok(())
                }
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }
}

//! Running host programs (systemctl, journalctl, xray) behind a trait tests can fake.

use std::io::{self, Read};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

const COMMAND_TIMEOUT: Duration = Duration::from_secs(90);

#[derive(Clone, Debug, Default)]
pub struct CmdOutput {
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
}

pub trait Exec {
    /// Run a program to completion and capture its output.
    fn run(&self, program: &str, args: &[&str], env: &[(&str, &str)]) -> io::Result<CmdOutput>;

    /// Pause between starting a service and checking it is still active.
    fn sleep(&self, duration: Duration) {
        std::thread::sleep(duration);
    }
}

/// Real processes, each killed after a generous timeout so a hung unit never wedges the agent.
pub struct RealExec;

impl Exec for RealExec {
    fn run(&self, program: &str, args: &[&str], env: &[(&str, &str)]) -> io::Result<CmdOutput> {
        let mut child = Command::new(program)
            .args(args)
            .envs(env.iter().copied())
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        let stdout = read_all(child.stdout.take());
        let stderr = read_all(child.stderr.take());
        let pid = child.id();
        let (done_tx, done_rx) = mpsc::channel();
        let waiter = std::thread::spawn(move || {
            let status = child.wait();
            let _ = done_tx.send(());
            status
        });
        if done_rx.recv_timeout(COMMAND_TIMEOUT).is_err() {
            // SAFETY: plain kill(2) on a child we spawned and have not reaped yet.
            unsafe {
                libc::kill(pid as libc::pid_t, libc::SIGKILL);
            }
        }
        let status = waiter
            .join()
            .map_err(|_| io::Error::other("waiter panicked"))??;
        let out = stdout
            .join()
            .map_err(|_| io::Error::other("reader panicked"))?;
        let err = stderr
            .join()
            .map_err(|_| io::Error::other("reader panicked"))?;
        Ok(CmdOutput {
            success: status.success(),
            stdout: String::from_utf8_lossy(&out).into_owned(),
            stderr: String::from_utf8_lossy(&err).into_owned(),
        })
    }
}

fn read_all<R: Read + Send + 'static>(pipe: Option<R>) -> std::thread::JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut pipe) = pipe {
            let _ = pipe.read_to_end(&mut buf);
        }
        buf
    })
}

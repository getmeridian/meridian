//! Running host programs (systemctl, journalctl, xray) behind a trait tests can fake.

use std::io::{self, Read};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

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

    /// A shorter command deadline for local readiness probes. Test hosts can use `run`.
    fn run_timeout(
        &self,
        program: &str,
        args: &[&str],
        env: &[(&str, &str)],
        _timeout: Duration,
    ) -> io::Result<CmdOutput> {
        self.run(program, args, env)
    }

    /// The monotonic clock used for readiness and collection freshness.
    fn now(&self) -> Instant {
        Instant::now()
    }

    /// Pause between local probes.
    fn sleep(&self, duration: Duration) {
        std::thread::sleep(duration);
    }
}

/// Real processes, each killed after a generous timeout so a hung unit never wedges the agent.
pub struct RealExec;

impl Exec for RealExec {
    fn run(&self, program: &str, args: &[&str], env: &[(&str, &str)]) -> io::Result<CmdOutput> {
        run_with_timeout(program, args, env, COMMAND_TIMEOUT)
    }

    fn run_timeout(
        &self,
        program: &str,
        args: &[&str],
        env: &[(&str, &str)],
        timeout: Duration,
    ) -> io::Result<CmdOutput> {
        run_with_timeout(program, args, env, timeout)
    }
}

/// One owner waits, kills, and reaps the child: a separate waiter could reap it just before a
/// timeout's kill(2), which would then hit whatever process reused the PID.
fn run_with_timeout(
    program: &str,
    args: &[&str],
    env: &[(&str, &str)],
    timeout: Duration,
) -> io::Result<CmdOutput> {
    let mut child = Command::new(program)
        .args(args)
        .envs(env.iter().copied())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let stdout = read_all(child.stdout.take());
    let stderr = read_all(child.stderr.take());
    let deadline = Instant::now() + timeout;
    let mut pause = Duration::from_millis(1);
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            // try_wait just saw it running and nothing else reaps it, so the PID is still ours.
            child.kill()?;
            break child.wait()?;
        }
        std::thread::sleep(pause.min(deadline.saturating_duration_since(Instant::now())));
        pause = (pause * 2).min(Duration::from_millis(50));
    };
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

fn read_all<R: Read + Send + 'static>(pipe: Option<R>) -> std::thread::JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut pipe) = pipe {
            let _ = pipe.read_to_end(&mut buf);
        }
        buf
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_command_past_its_timeout_is_killed_and_reaped() {
        let started = Instant::now();
        let slow = run_with_timeout("sleep", &["30"], &[], Duration::from_millis(100)).unwrap();
        assert!(!slow.success);
        assert!(started.elapsed() < Duration::from_secs(10));
        let quick =
            run_with_timeout("sh", &["-c", "echo hi"], &[], Duration::from_secs(30)).unwrap();
        assert!(quick.success);
        assert_eq!(quick.stdout, "hi\n");
    }
}

//! `netagent`: Meridian's node agent.

use std::path::PathBuf;
use std::process::ExitCode;

use meridian_agent::agent::{Agent, Options};
use meridian_agent::selfcheck::self_check;
use meridian_agent::system::RealExec;
use meridian_agent::{VERSION, info, log};

const USAGE: &str = "usage: netagent run [--bootstrap PATH] [--state-dir DIR] [--root DIR] [--debug]\n       netagent check [--bootstrap PATH] [--state-dir DIR]\n       netagent version";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("version") | Some("--version") => {
            println!("{VERSION}");
            ExitCode::SUCCESS
        }
        Some("run") => match parse_run(&args[1..]) {
            Ok(opts) => run(opts),
            Err(err) => {
                eprintln!("netagent: {err}\n{USAGE}");
                ExitCode::from(2)
            }
        },
        Some("check") => match parse_run(&args[1..]) {
            Ok(opts) => check(&opts),
            Err(err) => {
                eprintln!("netagent: {err}\n{USAGE}");
                ExitCode::from(2)
            }
        },
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

fn parse_run(args: &[String]) -> Result<Options, String> {
    let mut opts = Options::production();
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        let mut value = |name: &str| iter.next().cloned().ok_or(format!("{name} needs a value"));
        match arg.as_str() {
            "--bootstrap" => opts.bootstrap = PathBuf::from(value("--bootstrap")?),
            "--state-dir" => opts.state_dir = PathBuf::from(value("--state-dir")?),
            "--root" => opts.root = PathBuf::from(value("--root")?),
            "--debug" => log::set_debug(true),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if std::env::var_os("NETAGENT_DEBUG").is_some_and(|v| v == "1") {
        log::set_debug(true);
    }
    Ok(opts)
}

/// Print one JSON line when this build can take over the node's files; exit 1 when it cannot.
fn check(opts: &Options) -> ExitCode {
    match self_check(&opts.bootstrap, &opts.state_dir, opts.allow_http) {
        Ok(report) => {
            println!("{report}");
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("netagent: {err}");
            ExitCode::from(1)
        }
    }
}

fn run(opts: Options) -> ExitCode {
    let exec = RealExec;
    let mut agent = match Agent::new(opts, &exec) {
        Ok(agent) => agent,
        Err(err) => {
            eprintln!("netagent: {err}");
            return ExitCode::from(1);
        }
    };
    info!(
        "netagent {VERSION} starting for node {} (CA {})",
        agent.bootstrap.node_id,
        &agent.bootstrap.ca_sha256[..12]
    );
    agent.run()
}

//! Execute an arbitrary program with file-backed I/O and a separate control pipe.
use serde_json::{json, Value};
use std::{fs::File, io, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::Command,
    time,
};

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, io::Error> {
    value[key]
        .as_str()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, format!("Missing {key}")))
}

fn duration(value: Option<&Value>) -> Result<Option<Duration>, io::Error> {
    let seconds = match value {
        None | Some(Value::Null) => 0.0,
        Some(value) => value.as_f64().ok_or_else(|| io::Error::new(
            io::ErrorKind::InvalidInput, "timeout must be a number of seconds"))?,
    };
    if !seconds.is_finite() || !(0.0..=31_536_000.0).contains(&seconds) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput,
            "timeout must be nonnegative and at most one year; zero means no deadline"));
    }
    Ok(if seconds == 0.0 { None } else { Some(Duration::from_secs_f64(seconds)) })
}

pub async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut requests = BufReader::new(tokio::io::stdin()).lines();
    let Some(line) = requests.next_line().await? else {
        return Ok(());
    };
    let request: Value = serde_json::from_str(&line)?;
    let p = &request["params"];
    // Validate before opening output files or launching the program.
    let timeout = match duration(p.get("timeout")) {
        Ok(value) => value,
        Err(error) => {
            super::emit(&json!({"id":request["id"], "error":error.to_string()})).await?;
            return Ok(());
        }
    };
    let args = p["command"]
        .as_array()
        .ok_or("command must be an argument array")?;
    let argv: Vec<&str> = args
        .iter()
        .map(|v| v.as_str().ok_or("command arguments must be strings"))
        .collect::<Result<_, _>>()?;
    if argv.is_empty() {
        return Err("command must contain an executable".into());
    }
    let mut command = Command::new(argv[0]);
    command
        .args(&argv[1..])
        .current_dir(text(p, "cwd")?)
        .stdin(match p["stdin"].as_str() {
            Some(path) => Stdio::from(File::open(path)?),
            None => Stdio::null(),
        })
        .stdout(Stdio::from(File::create(text(p, "stdout")?)?))
        .stderr(Stdio::from(File::create(text(p, "stderr")?)?))
        .kill_on_drop(true);
    if let Some(values) = p["env"].as_object() {
        for (key, value) in values {
            command.env(
                key,
                value.as_str().ok_or("environment values must be strings")?,
            );
        }
    }
    #[cfg(unix)]
    {
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        command.creation_flags(0x00000200);
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            super::emit(&json!({"id":request["id"], "error":error.to_string()})).await?;
            return Ok(());
        }
    };
    let pid = child.id().unwrap();
    #[cfg(windows)]
    let tree = super::process_tree::Tree::attach(&child)?;
    let result = async {
        super::emit(&json!({"id":request["id"],"event":"started","pid":pid})).await?;
        let timer = time::sleep(timeout.unwrap_or(Duration::from_secs(86400)));
        tokio::pin!(timer);
        let response = loop {
            tokio::select! {
                status = child.wait() => {
                    let status = status?;
                    break json!({"id":request["id"],"result":{"exit_code":status.code().unwrap_or(-1)}});
                }
                message = requests.next_line() => {
                    let Some(line) = message? else { return Ok(()); };
                    let message: Value = serde_json::from_str(&line)?;
                    if message["method"] == "cancel" && message["params"]["id"] == request["id"] {
                        super::stop(&mut child).await;
                        break json!({"id":request["id"],"cancelled":true,"result":{"exit_code":-1}});
                    }
                }
                _ = &mut timer, if timeout.is_some() => {
                    super::stop(&mut child).await;
                    break json!({"id":request["id"],"error":"Program exceeded its configured duration","result":{"exit_code":-1}});
                }
            }
        };
        // A completed program must not leave descendants using its files or devices.
        #[cfg(unix)] unsafe { libc::kill(-(pid as i32), libc::SIGKILL); }
        #[cfg(windows)] drop(tree);
        super::emit(&response).await?;
        Ok::<(), Box<dyn std::error::Error>>(())
    }.await;
    super::stop(&mut child).await;
    result
}

#[cfg(test)]
mod tests {
    use super::duration;
    use serde_json::json;
    use std::time::Duration;

    #[test]
    fn optional_deadline_is_a_bounded_number() {
        assert_eq!(duration(None).unwrap(), None);
        assert_eq!(duration(Some(&json!(null))).unwrap(), None);
        assert_eq!(duration(Some(&json!(0))).unwrap(), None);
        assert_eq!(duration(Some(&json!(0.125))).unwrap(), Some(Duration::from_millis(125)));
        assert_eq!(duration(Some(&json!(31_536_000))).unwrap(), Some(Duration::from_secs(31_536_000)));
        for value in [json!(-1), json!(true), json!("5"), json!({}), json!(1e300)] {
            assert!(duration(Some(&value)).is_err(), "{value}");
        }
    }
}

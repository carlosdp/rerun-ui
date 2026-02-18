use std::io::{BufRead, BufReader, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, RwLock};
use std::thread;
use std::time::Duration;

use anyhow::Context;
use serde::{Deserialize, Serialize};

fn protocol_version() -> u32 {
    env!("RERUN_UI_PROTOCOL_VERSION").parse().unwrap_or(1)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ButtonConfig {
    pub id: String,
    pub label: String,
}

#[derive(Debug, Clone)]
pub struct KeyboardConfig {
    pub enabled: bool,
    pub poll_hz: f32,
}

impl Default for KeyboardConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            poll_hz: 30.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum ViewerCommand {
    Hello {
        protocol_version: u32,
        client_name: Option<String>,
    },
    SetButtons {
        buttons: Vec<ButtonConfig>,
    },
    SetKeyboardConfig {
        enabled: bool,
        poll_hz: f32,
    },
    Ping {
        nonce: Option<String>,
    },
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ViewerEvent {
    HelloAck {
        protocol_version: u32,
        custom_ui: bool,
    },
    ButtonClicked {
        button_id: String,
    },
    KeyboardState {
        pressed_keys: Vec<String>,
    },
    Pong {
        nonce: Option<String>,
    },
}

pub struct ControlShared {
    buttons: RwLock<Vec<ButtonConfig>>,
    keyboard_config: RwLock<KeyboardConfig>,
    event_tx: mpsc::Sender<ViewerEvent>,
    connected: AtomicBool,
}

impl ControlShared {
    fn new(event_tx: mpsc::Sender<ViewerEvent>) -> Self {
        Self {
            buttons: RwLock::new(Vec::new()),
            keyboard_config: RwLock::new(KeyboardConfig::default()),
            event_tx,
            connected: AtomicBool::new(false),
        }
    }

    pub fn set_buttons(&self, buttons: Vec<ButtonConfig>) {
        if let Ok(mut current) = self.buttons.write() {
            *current = buttons;
        }
    }

    pub fn buttons(&self) -> Vec<ButtonConfig> {
        self.buttons
            .read()
            .map(|buttons| buttons.clone())
            .unwrap_or_default()
    }

    pub fn set_keyboard_config(&self, enabled: bool, poll_hz: f32) {
        if let Ok(mut config) = self.keyboard_config.write() {
            config.enabled = enabled;
            config.poll_hz = poll_hz.max(1.0);
        }
    }

    pub fn keyboard_config(&self) -> KeyboardConfig {
        self.keyboard_config
            .read()
            .map(|config| config.clone())
            .unwrap_or_default()
    }

    pub fn emit_event(&self, event: ViewerEvent) {
        if self.connected.load(Ordering::SeqCst) {
            let _ = self.event_tx.send(event);
        }
    }
}

pub struct ControlBridge {
    pub shared: Arc<ControlShared>,
    #[allow(dead_code)]
    server_thread: thread::JoinHandle<()>,
}

pub fn start_control_server(control_port: u16) -> anyhow::Result<ControlBridge> {
    let (event_tx, event_rx) = mpsc::channel::<ViewerEvent>();
    let shared = Arc::new(ControlShared::new(event_tx));

    let server_shared = Arc::clone(&shared);
    let server_thread = thread::Builder::new()
        .name("rerun-ui-control-server".to_owned())
        .spawn(move || {
            if let Err(err) = run_server(control_port, server_shared, event_rx) {
                eprintln!("rerun_ui control server failed: {err}");
            }
        })
        .context("failed to spawn control server thread")?;

    Ok(ControlBridge {
        shared,
        server_thread,
    })
}

fn run_server(
    control_port: u16,
    shared: Arc<ControlShared>,
    event_rx: mpsc::Receiver<ViewerEvent>,
) -> anyhow::Result<()> {
    let listener = TcpListener::bind(("127.0.0.1", control_port))
        .with_context(|| format!("failed to bind control port {control_port}"))?;

    loop {
        let (stream, _addr) = listener.accept()?;
        if let Err(err) = handle_connection(stream, &shared, &event_rx) {
            eprintln!("rerun_ui control connection error: {err}");
            shared.connected.store(false, Ordering::SeqCst);
        }
    }
}

fn handle_connection(
    stream: TcpStream,
    shared: &Arc<ControlShared>,
    event_rx: &mpsc::Receiver<ViewerEvent>,
) -> anyhow::Result<()> {
    while event_rx.try_recv().is_ok() {}

    let (cmd_tx, cmd_rx) = mpsc::channel::<ViewerCommand>();
    let reader_stream = stream.try_clone()?;

    let reader_thread = thread::Builder::new()
        .name("rerun-ui-control-reader".to_owned())
        .spawn(move || read_commands(reader_stream, cmd_tx))
        .context("failed to spawn control reader thread")?;

    shared.connected.store(true, Ordering::SeqCst);
    let mut writer = stream;

    loop {
        let mut closed = false;

        loop {
            match cmd_rx.try_recv() {
                Ok(command) => {
                    if let Err(err) = handle_command(command, shared, &mut writer) {
                        eprintln!("rerun_ui command handling failed: {err}");
                        closed = true;
                        break;
                    }
                }
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => {
                    closed = true;
                    break;
                }
            }
        }

        if closed {
            break;
        }

        match event_rx.recv_timeout(Duration::from_millis(50)) {
            Ok(event) => {
                if let Err(err) = write_event(&mut writer, &event) {
                    eprintln!("rerun_ui event write failed: {err}");
                    break;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }

    shared.connected.store(false, Ordering::SeqCst);
    let _ = writer.shutdown(Shutdown::Both);
    let _ = reader_thread.join();

    Ok(())
}

fn read_commands(stream: TcpStream, cmd_tx: mpsc::Sender<ViewerCommand>) {
    let mut reader = BufReader::new(stream);
    let mut line = String::new();

    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => {
                let payload = line.trim();
                if payload.is_empty() {
                    continue;
                }

                match serde_json::from_str::<ViewerCommand>(payload) {
                    Ok(command) => {
                        if cmd_tx.send(command).is_err() {
                            break;
                        }
                    }
                    Err(err) => {
                        eprintln!("rerun_ui dropped invalid command: {err}");
                    }
                }
            }
            Err(err) => {
                eprintln!("rerun_ui control read failed: {err}");
                break;
            }
        }
    }
}

fn handle_command(
    command: ViewerCommand,
    shared: &ControlShared,
    writer: &mut TcpStream,
) -> anyhow::Result<()> {
    match command {
        ViewerCommand::Hello { .. } => {
            write_event(
                writer,
                &ViewerEvent::HelloAck {
                    protocol_version: protocol_version(),
                    custom_ui: true,
                },
            )?;
        }
        ViewerCommand::SetButtons { buttons } => {
            shared.set_buttons(buttons);
        }
        ViewerCommand::SetKeyboardConfig { enabled, poll_hz } => {
            shared.set_keyboard_config(enabled, poll_hz);
        }
        ViewerCommand::Ping { nonce } => {
            write_event(writer, &ViewerEvent::Pong { nonce })?;
        }
    }

    Ok(())
}

fn write_event(writer: &mut TcpStream, event: &ViewerEvent) -> anyhow::Result<()> {
    let mut payload = serde_json::to_string(event)?;
    payload.push('\n');
    writer.write_all(payload.as_bytes())?;
    writer.flush()?;
    Ok(())
}

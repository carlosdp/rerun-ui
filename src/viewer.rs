use std::time::{Duration, Instant};

use anyhow::Context;
use rerun::external::{
    eframe, egui, re_crash_handler, re_grpc_server, re_log, re_memory, re_viewer, tokio,
};

use crate::ipc::{self, ControlShared, ViewerEvent};

#[global_allocator]
static GLOBAL: re_memory::AccountingAllocator<mimalloc::MiMalloc> =
    re_memory::AccountingAllocator::new(mimalloc::MiMalloc);

pub fn run_viewer_process(grpc_port: u16, control_port: u16) -> anyhow::Result<()> {
    // When running inside a Python host process, the actual process main thread
    // is not necessarily named "main" from Rust's perspective in debug builds.
    // Use a debug-only fallback token to avoid false positives from that assertion.
    #[cfg(debug_assertions)]
    let main_thread_token = re_viewer::MainThreadToken::i_promise_i_am_only_using_this_for_a_test();
    #[cfg(not(debug_assertions))]
    let main_thread_token = re_viewer::MainThreadToken::i_promise_i_am_on_the_main_thread();

    re_log::setup_logging();
    re_crash_handler::install_crash_handlers(re_viewer::build_info());

    let control = ipc::start_control_server(control_port)?;

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .context("failed to build tokio runtime")?;
    let _runtime_guard = runtime.enter();

    let rx = re_grpc_server::spawn_with_recv(
        format!("127.0.0.1:{grpc_port}").parse()?,
        Default::default(),
        re_grpc_server::shutdown::never(),
    );

    let mut native_options = re_viewer::native::eframe_options(None);
    native_options.viewport = native_options
        .viewport
        .with_app_id("rerun_ui_custom_viewer");

    let startup_options = re_viewer::StartupOptions::default();
    let app_env = re_viewer::AppEnvironment::Custom("rerun_ui".to_owned());
    let window_title = "rerun_ui viewer";

    eframe::run_native(
        window_title,
        native_options,
        Box::new(move |cc| {
            re_viewer::customize_eframe_and_setup_renderer(cc)?;

            let mut rerun_app = re_viewer::App::new(
                main_thread_token,
                re_viewer::build_info(),
                app_env,
                startup_options,
                cc,
                None,
                re_viewer::AsyncRuntimeHandle::from_current_tokio_runtime_or_wasmbindgen()?,
            );
            rerun_app.add_log_receiver(rx);

            Ok(Box::new(CustomViewerApp::new(rerun_app, control.shared)))
        }),
    )?;

    Ok(())
}

struct CustomViewerApp {
    rerun_app: re_viewer::App,
    control: std::sync::Arc<ControlShared>,
    keyboard_state: KeyboardState,
}

impl CustomViewerApp {
    fn new(rerun_app: re_viewer::App, control: std::sync::Arc<ControlShared>) -> Self {
        Self {
            rerun_app,
            control,
            keyboard_state: KeyboardState::default(),
        }
    }

    fn render_controls(&mut self, ctx: &egui::Context) {
        egui::TopBottomPanel::bottom("rerun_ui_controls")
            .resizable(false)
            .default_height(92.0)
            .show(ctx, |ui| {
                let buttons = self.control.buttons();
                ui.horizontal_wrapped(|ui| {
                    ui.set_min_height(72.0);
                    if buttons.is_empty() {
                        ui.label("No custom controls registered");
                    }

                    for button in &buttons {
                        if ui
                            .add_sized([220.0, 56.0], egui::Button::new(&button.label))
                            .clicked()
                        {
                            self.control.emit_event(ViewerEvent::ButtonClicked {
                                button_id: button.id.clone(),
                            });
                        }
                    }
                });
            });
    }

    fn emit_keyboard_events(&mut self, ctx: &egui::Context) {
        let config = self.control.keyboard_config();
        let now = Instant::now();

        if !config.enabled {
            if !self.keyboard_state.last_keys.is_empty() {
                self.control.emit_event(ViewerEvent::KeyboardState {
                    pressed_keys: Vec::new(),
                });
                self.keyboard_state.last_keys.clear();
            }
            return;
        }

        let keys = collect_normalized_keys(ctx);
        let changed = keys != self.keyboard_state.last_keys;
        let interval = Duration::from_secs_f32(1.0 / config.poll_hz.max(1.0));
        let periodic_due =
            !keys.is_empty() && now.duration_since(self.keyboard_state.last_emit) >= interval;

        if changed || periodic_due {
            self.control.emit_event(ViewerEvent::KeyboardState {
                pressed_keys: keys.clone(),
            });
            self.keyboard_state.last_keys = keys;
            self.keyboard_state.last_emit = now;
        }
    }
}

impl eframe::App for CustomViewerApp {
    fn save(&mut self, storage: &mut dyn eframe::Storage) {
        self.rerun_app.save(storage);
    }

    fn update(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        self.render_controls(ctx);
        self.emit_keyboard_events(ctx);
        self.rerun_app.update(ctx, frame);
    }
}

#[derive(Debug)]
struct KeyboardState {
    last_keys: Vec<String>,
    last_emit: Instant,
}

impl Default for KeyboardState {
    fn default() -> Self {
        Self {
            last_keys: Vec::new(),
            last_emit: Instant::now(),
        }
    }
}

fn collect_normalized_keys(ctx: &egui::Context) -> Vec<String> {
    let mut keys: Vec<String> = ctx.input(|input| {
        input
            .keys_down
            .iter()
            .map(|key| normalize_key_name(format!("{key:?}")))
            .collect()
    });
    keys.sort_unstable();
    keys.dedup();
    keys
}

fn normalize_key_name(raw: String) -> String {
    let mut out = String::with_capacity(raw.len() + 4);
    let mut prev_is_upper = false;

    for (idx, ch) in raw.chars().enumerate() {
        if ch.is_ascii_alphanumeric() {
            let is_upper = ch.is_ascii_uppercase();
            if idx > 0 && is_upper && !prev_is_upper {
                out.push('_');
            }
            out.push(ch.to_ascii_uppercase());
            prev_is_upper = is_upper;
        }
    }

    out
}

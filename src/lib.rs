mod ipc;
mod rive;
mod rive_ffi;
mod viewer;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::sync::{Mutex, MutexGuard};

#[pyclass(module = "rerun_ui._rerun_ui")]
struct NativeRiveRenderer {
    inner: Mutex<rive::NativeRenderer>,
}

#[pymethods]
impl NativeRiveRenderer {
    #[new]
    #[pyo3(signature = (*, riv_path, artboard, state_machine, width, height))]
    fn new(
        riv_path: String,
        artboard: String,
        state_machine: String,
        width: u32,
        height: u32,
    ) -> PyResult<Self> {
        let inner = rive::NativeRenderer::new(&riv_path, &artboard, &state_machine, width, height)
            .map_err(map_native_error)?;
        Ok(Self {
            inner: Mutex::new(inner),
        })
    }

    #[getter]
    fn backend_kind(&self) -> &'static str {
        "native"
    }

    fn set_bool(&self, input_name: &str, value: bool) -> PyResult<()> {
        let mut renderer = self.lock_renderer()?;
        renderer
            .set_bool(input_name, value)
            .map_err(map_native_error)
    }

    fn set_number(&self, input_name: &str, value: f32) -> PyResult<()> {
        let mut renderer = self.lock_renderer()?;
        renderer
            .set_number(input_name, value)
            .map_err(map_native_error)
    }

    fn fire_trigger(&self, input_name: &str) -> PyResult<()> {
        let mut renderer = self.lock_renderer()?;
        renderer.fire_trigger(input_name).map_err(map_native_error)
    }

    fn advance(&self, dt_s: f32) -> PyResult<()> {
        let mut renderer = self.lock_renderer()?;
        renderer.advance(dt_s).map_err(map_native_error)
    }

    fn render_rgba<'py>(&self, py: Python<'py>) -> PyResult<(Py<PyBytes>, u32, u32)> {
        let mut renderer = self.lock_renderer()?;
        let width = renderer.width();
        let height = renderer.height();
        let rgba = renderer.render_rgba().map_err(map_native_error)?;
        Ok((PyBytes::new(py, rgba.as_slice()).unbind(), width, height))
    }

    fn close(&self) -> PyResult<()> {
        let mut renderer = self.lock_renderer()?;
        renderer.close();
        Ok(())
    }
}

impl NativeRiveRenderer {
    fn lock_renderer(&self) -> PyResult<MutexGuard<'_, rive::NativeRenderer>> {
        self.inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("native Rive renderer mutex was poisoned"))
    }
}

#[pyfunction]
fn run_viewer_process(grpc_port: u16, control_port: u16) -> PyResult<()> {
    viewer::run_viewer_process(grpc_port, control_port)
        .map_err(|err| PyRuntimeError::new_err(err.to_string()))
}

#[pyfunction]
fn expected_rerun_major_minor() -> &'static str {
    env!("RERUN_UI_EXPECTED_RERUN_MAJOR_MINOR")
}

#[pyfunction]
fn protocol_version() -> u32 {
    env!("RERUN_UI_PROTOCOL_VERSION").parse().unwrap_or(1)
}

#[pymodule]
fn _rerun_ui(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<NativeRiveRenderer>()?;
    module.add_function(wrap_pyfunction!(run_viewer_process, module)?)?;
    module.add_function(wrap_pyfunction!(expected_rerun_major_minor, module)?)?;
    module.add_function(wrap_pyfunction!(protocol_version, module)?)?;
    Ok(())
}

fn map_native_error(err: anyhow::Error) -> PyErr {
    PyRuntimeError::new_err(err.to_string())
}

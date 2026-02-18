mod ipc;
mod viewer;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

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
    module.add_function(wrap_pyfunction!(run_viewer_process, module)?)?;
    module.add_function(wrap_pyfunction!(expected_rerun_major_minor, module)?)?;
    module.add_function(wrap_pyfunction!(protocol_version, module)?)?;
    Ok(())
}

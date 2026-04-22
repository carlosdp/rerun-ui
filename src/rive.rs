use crate::rive_ffi;
use anyhow::{anyhow, bail, Context, Result};
use std::ffi::{CStr, CString};
use std::ptr;

pub struct NativeRenderer {
    handle: *mut rive_ffi::RiveRendererHandle,
    width: u32,
    height: u32,
}

// SAFETY: The underlying native renderer handle is only accessed through the
// outer `Mutex<NativeRenderer>` in `NativeRiveRenderer`, so calls into the C++
// runtime are serialized. The raw pointer itself owns no thread affinity.
unsafe impl Send for NativeRenderer {}

impl NativeRenderer {
    pub fn new(
        riv_path: &str,
        artboard: &str,
        state_machine: &str,
        width: u32,
        height: u32,
    ) -> Result<Self> {
        let riv_path = c_string(riv_path, "riv_path")?;
        let artboard = c_string(artboard, "artboard")?;
        let state_machine = c_string(state_machine, "state_machine")?;
        let handle = unsafe {
            let mut error = ptr::null_mut();
            let handle = rive_ffi::rive_renderer_create(
                riv_path.as_ptr(),
                artboard.as_ptr(),
                state_machine.as_ptr(),
                width,
                height,
                &mut error,
            );
            ensure_ok(!handle.is_null(), error)?;
            handle
        };

        Ok(Self {
            handle,
            width,
            height,
        })
    }

    pub fn width(&self) -> u32 {
        self.width
    }

    pub fn height(&self) -> u32 {
        self.height
    }

    pub fn set_bool(&mut self, input_name: &str, value: bool) -> Result<()> {
        let input_name = c_string(input_name, "input_name")?;
        self.with_handle(|handle, error| unsafe {
            rive_ffi::rive_renderer_set_bool(handle, input_name.as_ptr(), value, error)
        })
    }

    pub fn set_number(&mut self, input_name: &str, value: f32) -> Result<()> {
        if !value.is_finite() {
            bail!("value must be finite");
        }
        let input_name = c_string(input_name, "input_name")?;
        self.with_handle(|handle, error| unsafe {
            rive_ffi::rive_renderer_set_number(handle, input_name.as_ptr(), value, error)
        })
    }

    pub fn fire_trigger(&mut self, input_name: &str) -> Result<()> {
        let input_name = c_string(input_name, "input_name")?;
        self.with_handle(|handle, error| unsafe {
            rive_ffi::rive_renderer_fire_trigger(handle, input_name.as_ptr(), error)
        })
    }

    pub fn advance(&mut self, dt_s: f32) -> Result<()> {
        if !dt_s.is_finite() || dt_s < 0.0 {
            bail!("dt_s must be a finite, non-negative number");
        }
        self.with_handle(|handle, error| unsafe {
            rive_ffi::rive_renderer_advance(handle, dt_s, error)
        })
    }

    pub fn render_rgba(&mut self) -> Result<Vec<u8>> {
        let mut rgba = vec![0u8; (self.width as usize) * (self.height as usize) * 4];
        self.with_handle(|handle, error| unsafe {
            rive_ffi::rive_renderer_render_rgba(handle, rgba.as_mut_ptr(), rgba.len() as u32, error)
        })?;
        Ok(rgba)
    }

    pub fn close(&mut self) {
        if self.handle.is_null() {
            return;
        }
        unsafe {
            rive_ffi::rive_renderer_destroy(self.handle);
        }
        self.handle = ptr::null_mut();
    }

    fn with_handle<F>(&mut self, f: F) -> Result<()>
    where
        F: FnOnce(*mut rive_ffi::RiveRendererHandle, *mut *mut std::os::raw::c_char) -> bool,
    {
        if self.handle.is_null() {
            bail!("Rive renderer is closed");
        }

        unsafe {
            let mut error = ptr::null_mut();
            let ok = f(self.handle, &mut error);
            ensure_ok(ok, error)
        }
    }
}

impl Drop for NativeRenderer {
    fn drop(&mut self) {
        self.close();
    }
}

fn c_string(value: &str, field_name: &str) -> Result<CString> {
    CString::new(value).with_context(|| format!("{field_name} must not contain embedded NUL bytes"))
}

unsafe fn ensure_ok(ok: bool, error: *mut std::os::raw::c_char) -> Result<()> {
    if ok {
        return Ok(());
    }
    if error.is_null() {
        return Err(anyhow!("native Rive backend returned an unknown error"));
    }

    let message = unsafe { CStr::from_ptr(error) }
        .to_str()
        .map(|value| value.to_owned())
        .unwrap_or_else(|_| "native Rive backend returned a non-utf8 error".to_owned());
    unsafe {
        rive_ffi::rive_renderer_free_error(error);
    }
    Err(anyhow!(message))
}

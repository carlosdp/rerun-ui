use std::os::raw::{c_char, c_float, c_uchar, c_uint};

#[repr(C)]
pub struct RiveRendererHandle {
    _private: [u8; 0],
}

unsafe extern "C" {
    pub fn rive_renderer_create(
        riv_path: *const c_char,
        artboard: *const c_char,
        state_machine: *const c_char,
        width: c_uint,
        height: c_uint,
        error_out: *mut *mut c_char,
    ) -> *mut RiveRendererHandle;

    pub fn rive_renderer_destroy(handle: *mut RiveRendererHandle);

    pub fn rive_renderer_set_bool(
        handle: *mut RiveRendererHandle,
        input_name: *const c_char,
        value: bool,
        error_out: *mut *mut c_char,
    ) -> bool;

    pub fn rive_renderer_set_number(
        handle: *mut RiveRendererHandle,
        input_name: *const c_char,
        value: c_float,
        error_out: *mut *mut c_char,
    ) -> bool;

    pub fn rive_renderer_fire_trigger(
        handle: *mut RiveRendererHandle,
        input_name: *const c_char,
        error_out: *mut *mut c_char,
    ) -> bool;

    pub fn rive_renderer_advance(
        handle: *mut RiveRendererHandle,
        dt_s: c_float,
        error_out: *mut *mut c_char,
    ) -> bool;

    pub fn rive_renderer_render_rgba(
        handle: *mut RiveRendererHandle,
        out_rgba: *mut c_uchar,
        out_len: c_uint,
        error_out: *mut *mut c_char,
    ) -> bool;

    pub fn rive_renderer_free_error(error: *mut c_char);
}

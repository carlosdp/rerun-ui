use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock, RwLock};

use re_log_types::EntityPath;
use re_viewer_context::ViewId;

use crate::picking::PickingContext;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PointerEventType {
    Press,
    Release,
    Click,
    Drag,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PointerButton {
    Primary,
    Secondary,
    Middle,
}

#[derive(Debug, Clone)]
pub struct Pointer3DEvent {
    pub event_type: PointerEventType,
    pub button: PointerButton,
    pub view_id: String,
    pub space_origin: String,
    pub pointer_in_ui: glam::Vec2,
    pub pointer_in_view: glam::Vec2,
    pub ray_origin: glam::Vec3,
    pub ray_direction: glam::Vec3,
    pub projected_position: Option<glam::Vec3>,
    pub drag_delta: Option<glam::Vec2>,
}

pub type PointerEventCallback = Arc<dyn Fn(Pointer3DEvent) + Send + Sync + 'static>;

fn callback_cell() -> &'static RwLock<Option<PointerEventCallback>> {
    static POINTER_EVENT_CALLBACK: OnceLock<RwLock<Option<PointerEventCallback>>> = OnceLock::new();
    POINTER_EVENT_CALLBACK.get_or_init(|| RwLock::new(None))
}

fn listeners_enabled_cell() -> &'static AtomicBool {
    static POINTER_LISTENERS_ENABLED: OnceLock<AtomicBool> = OnceLock::new();
    POINTER_LISTENERS_ENABLED.get_or_init(|| AtomicBool::new(false))
}

pub fn set_pointer_event_callback(callback: Option<PointerEventCallback>) {
    if let Ok(mut callback_slot) = callback_cell().write() {
        *callback_slot = callback;
    }
}

pub fn set_pointer_listeners_enabled(enabled: bool) {
    listeners_enabled_cell().store(enabled, Ordering::SeqCst);
}

pub fn pointer_listeners_enabled() -> bool {
    listeners_enabled_cell().load(Ordering::SeqCst)
}

pub fn should_capture_interactions(response: &egui::Response) -> bool {
    if !pointer_listeners_enabled() {
        return false;
    }

    if !response
        .ctx
        .input(|input| input.modifiers.command || input.modifiers.mac_cmd)
    {
        return false;
    }

    response.contains_pointer()
        || response.hovered()
        || response.is_pointer_button_down_on()
        || response.dragged()
        || response.drag_stopped()
}

fn dispatch(event: Pointer3DEvent) {
    let callback = callback_cell()
        .read()
        .ok()
        .and_then(|callback_slot| callback_slot.clone());
    if let Some(callback) = callback {
        callback(event);
    }
}

fn emit_for_button(
    response: &egui::Response,
    egui_button: egui::PointerButton,
    button: PointerButton,
    view_id: &str,
    space_origin: &str,
    pointer_in_ui: glam::Vec2,
    pointer_in_view: glam::Vec2,
    ray_origin: glam::Vec3,
    ray_direction: glam::Vec3,
    projected_position: Option<glam::Vec3>,
) {
    let button_pressed = response
        .ctx
        .input(|input| input.pointer.button_pressed(egui_button));
    let button_released = response
        .ctx
        .input(|input| input.pointer.button_released(egui_button));

    let make_event = |event_type, drag_delta| Pointer3DEvent {
        event_type,
        button,
        view_id: view_id.to_owned(),
        space_origin: space_origin.to_owned(),
        pointer_in_ui,
        pointer_in_view,
        ray_origin,
        ray_direction,
        projected_position,
        drag_delta,
    };

    if button_pressed && response.contains_pointer() {
        dispatch(make_event(PointerEventType::Press, None));
    }

    if response.dragged_by(egui_button) {
        let delta = response.drag_delta();
        dispatch(make_event(
            PointerEventType::Drag,
            Some(glam::vec2(delta.x, delta.y)),
        ));
    }

    let released_on_response =
        response.drag_stopped_by(egui_button) || (button_released && response.contains_pointer());
    if released_on_response {
        dispatch(make_event(PointerEventType::Release, None));
    }

    if response.clicked_by(egui_button) {
        dispatch(make_event(PointerEventType::Click, None));
    }
}

pub fn emit_pointer_events(
    view_id: ViewId,
    space_origin: &EntityPath,
    response: &egui::Response,
    picking_context: &PickingContext,
    projected_position: Option<glam::Vec3>,
) {
    if !pointer_listeners_enabled() {
        return;
    }

    let has_callback = callback_cell()
        .read()
        .map(|callback_slot| callback_slot.is_some())
        .unwrap_or(false);
    if !has_callback {
        return;
    }

    let pointer_in_ui = picking_context.pointer_in_ui;
    let pointer_in_view = glam::vec2(
        pointer_in_ui.x - response.rect.left(),
        pointer_in_ui.y - response.rect.top(),
    );

    let ray_origin = picking_context.ray_in_world.origin;
    let ray_direction = picking_context.ray_in_world.dir;
    let view_id = format!("{view_id:?}");
    let space_origin = space_origin.to_string();

    emit_for_button(
        response,
        egui::PointerButton::Primary,
        PointerButton::Primary,
        &view_id,
        &space_origin,
        pointer_in_ui,
        pointer_in_view,
        ray_origin,
        ray_direction,
        projected_position,
    );
    emit_for_button(
        response,
        egui::PointerButton::Secondary,
        PointerButton::Secondary,
        &view_id,
        &space_origin,
        pointer_in_ui,
        pointer_in_view,
        ray_origin,
        ray_direction,
        projected_position,
    );
    emit_for_button(
        response,
        egui::PointerButton::Middle,
        PointerButton::Middle,
        &view_id,
        &space_origin,
        pointer_in_ui,
        pointer_in_view,
        ray_origin,
        ray_direction,
        projected_position,
    );
}

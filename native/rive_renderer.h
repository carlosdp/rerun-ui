#pragma once

#include <cstdint>

extern "C" {

struct RiveRendererHandle;

RiveRendererHandle* rive_renderer_create(const char* riv_path,
                                         const char* artboard,
                                         const char* state_machine,
                                         uint32_t width,
                                         uint32_t height,
                                         char** error_out);

void rive_renderer_destroy(RiveRendererHandle* handle);

bool rive_renderer_set_bool(RiveRendererHandle* handle,
                            const char* input_name,
                            bool value,
                            char** error_out);

bool rive_renderer_set_number(RiveRendererHandle* handle,
                              const char* input_name,
                              float value,
                              char** error_out);

bool rive_renderer_fire_trigger(RiveRendererHandle* handle,
                                const char* input_name,
                                char** error_out);

bool rive_renderer_advance(RiveRendererHandle* handle, float dt_s, char** error_out);

bool rive_renderer_render_rgba(RiveRendererHandle* handle,
                               uint8_t* out_rgba,
                               uint32_t out_len,
                               char** error_out);

void rive_renderer_free_error(char* error);

}

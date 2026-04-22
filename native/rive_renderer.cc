#include "rive_renderer.h"

#include <cairo/cairo.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "rive/animation/state_machine_input_instance.hpp"
#include "rive/animation/state_machine_instance.hpp"
#include "rive/artboard.hpp"
#include "rive/file.hpp"
#include "rive/factory.hpp"
#include "rive/math/raw_path.hpp"
#include "rive/refcnt.hpp"
#include "rive/renderer.hpp"
#include "rive/shapes/paint/blend_mode.hpp"
#include "rive/shapes/paint/color.hpp"
#include "rive/shapes/paint/image_sampler.hpp"
#include "rive/shapes/paint/stroke_cap.hpp"
#include "rive/shapes/paint/stroke_join.hpp"
#include "utils/factory_utils.hpp"

namespace {

using rive::AABB;
using rive::Alignment;
using rive::ArtboardInstance;
using rive::BlendMode;
using rive::ColorInt;
using rive::Factory;
using rive::FillRule;
using rive::Fit;
using rive::ImageSampler;
using rive::Mat2D;
using rive::RawPath;
using rive::RenderImage;
using rive::RenderPaint;
using rive::RenderPaintStyle;
using rive::RenderPath;
using rive::RenderShader;
using rive::Renderer;
using rive::SMIBool;
using rive::SMINumber;
using rive::SMITrigger;
using rive::StateMachineInstance;
using rive::StrokeCap;
using rive::StrokeJoin;

void set_error(char** error_out, const std::string& message) {
    if (error_out == nullptr) {
        return;
    }
    char* buffer = static_cast<char*>(std::malloc(message.size() + 1));
    if (buffer == nullptr) {
        return;
    }
    std::memcpy(buffer, message.c_str(), message.size() + 1);
    *error_out = buffer;
}

std::vector<uint8_t> read_file_bytes(const char* path) {
    if (path == nullptr || path[0] == '\0') {
        throw std::runtime_error("riv_path must be a non-empty string");
    }

    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error(std::string("failed to open Rive asset: ") + path);
    }

    return std::vector<uint8_t>((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
}

void ensure_name(const char* value, const char* field_name) {
    if (value == nullptr || value[0] == '\0') {
        throw std::runtime_error(std::string(field_name) + " must be a non-empty string");
    }
}

cairo_fill_rule_t to_cairo_fill_rule(FillRule fill_rule) {
    switch (fill_rule) {
        case FillRule::evenOdd:
            return CAIRO_FILL_RULE_EVEN_ODD;
        case FillRule::nonZero:
        case FillRule::clockwise:
        default:
            return CAIRO_FILL_RULE_WINDING;
    }
}

cairo_line_cap_t to_cairo_line_cap(StrokeCap cap) {
    switch (cap) {
        case StrokeCap::round:
            return CAIRO_LINE_CAP_ROUND;
        case StrokeCap::square:
            return CAIRO_LINE_CAP_SQUARE;
        case StrokeCap::butt:
        default:
            return CAIRO_LINE_CAP_BUTT;
    }
}

cairo_line_join_t to_cairo_line_join(StrokeJoin join) {
    switch (join) {
        case StrokeJoin::round:
            return CAIRO_LINE_JOIN_ROUND;
        case StrokeJoin::bevel:
            return CAIRO_LINE_JOIN_BEVEL;
        case StrokeJoin::miter:
        default:
            return CAIRO_LINE_JOIN_MITER;
    }
}

cairo_operator_t to_cairo_operator(BlendMode mode) {
    switch (mode) {
        case BlendMode::screen:
            return CAIRO_OPERATOR_SCREEN;
        case BlendMode::overlay:
            return CAIRO_OPERATOR_OVERLAY;
        case BlendMode::darken:
            return CAIRO_OPERATOR_DARKEN;
        case BlendMode::lighten:
            return CAIRO_OPERATOR_LIGHTEN;
        case BlendMode::colorDodge:
            return CAIRO_OPERATOR_COLOR_DODGE;
        case BlendMode::colorBurn:
            return CAIRO_OPERATOR_COLOR_BURN;
        case BlendMode::hardLight:
            return CAIRO_OPERATOR_HARD_LIGHT;
        case BlendMode::softLight:
            return CAIRO_OPERATOR_SOFT_LIGHT;
        case BlendMode::difference:
            return CAIRO_OPERATOR_DIFFERENCE;
        case BlendMode::exclusion:
            return CAIRO_OPERATOR_EXCLUSION;
        case BlendMode::multiply:
            return CAIRO_OPERATOR_MULTIPLY;
        case BlendMode::hue:
            return CAIRO_OPERATOR_HSL_HUE;
        case BlendMode::saturation:
            return CAIRO_OPERATOR_HSL_SATURATION;
        case BlendMode::color:
            return CAIRO_OPERATOR_HSL_COLOR;
        case BlendMode::luminosity:
            return CAIRO_OPERATOR_HSL_LUMINOSITY;
        case BlendMode::srcOver:
        default:
            return CAIRO_OPERATOR_OVER;
    }
}

struct RgbaD {
    double r;
    double g;
    double b;
    double a;
};

RgbaD unpack_color(ColorInt color, double opacity_scale = 1.0) {
    return {
        static_cast<double>(rive::colorRed(color)) / 255.0,
        static_cast<double>(rive::colorGreen(color)) / 255.0,
        static_cast<double>(rive::colorBlue(color)) / 255.0,
        (static_cast<double>(rive::colorAlpha(color)) / 255.0) * opacity_scale,
    };
}

class CairoGradientShader : public RenderShader {
  public:
    virtual cairo_pattern_t* make_pattern(double opacity) const = 0;
};

class CairoLinearGradientShader : public CairoGradientShader {
  public:
    CairoLinearGradientShader(float sx,
                              float sy,
                              float ex,
                              float ey,
                              const ColorInt colors[],
                              const float stops[],
                              size_t count)
        : start_x_(sx), start_y_(sy), end_x_(ex), end_y_(ey), colors_(colors, colors + count), stops_(stops, stops + count) {}

    cairo_pattern_t* make_pattern(double opacity) const override {
        cairo_pattern_t* pattern = cairo_pattern_create_linear(start_x_, start_y_, end_x_, end_y_);
        for (size_t index = 0; index < colors_.size(); ++index) {
            const auto rgba = unpack_color(colors_[index], opacity);
            cairo_pattern_add_color_stop_rgba(pattern, stops_[index], rgba.r, rgba.g, rgba.b, rgba.a);
        }
        return pattern;
    }

  private:
    double start_x_;
    double start_y_;
    double end_x_;
    double end_y_;
    std::vector<ColorInt> colors_;
    std::vector<float> stops_;
};

class CairoRadialGradientShader : public CairoGradientShader {
  public:
    CairoRadialGradientShader(float cx,
                              float cy,
                              float radius,
                              const ColorInt colors[],
                              const float stops[],
                              size_t count)
        : center_x_(cx), center_y_(cy), radius_(radius), colors_(colors, colors + count), stops_(stops, stops + count) {}

    cairo_pattern_t* make_pattern(double opacity) const override {
        cairo_pattern_t* pattern = cairo_pattern_create_radial(center_x_, center_y_, 0.0, center_x_, center_y_, radius_);
        for (size_t index = 0; index < colors_.size(); ++index) {
            const auto rgba = unpack_color(colors_[index], opacity);
            cairo_pattern_add_color_stop_rgba(pattern, stops_[index], rgba.r, rgba.g, rgba.b, rgba.a);
        }
        return pattern;
    }

  private:
    double center_x_;
    double center_y_;
    double radius_;
    std::vector<ColorInt> colors_;
    std::vector<float> stops_;
};

class CairoRenderPath : public RenderPath {
  public:
    CairoRenderPath() = default;

    CairoRenderPath(RawPath path, FillRule fill_rule) : path_(std::move(path)), fill_rule_(fill_rule) {}

    void addRenderPath(const RenderPath* path, const Mat2D& transform) override {
        const auto* cairo_path = static_cast<const CairoRenderPath*>(path);
        path_.addPath(cairo_path->path_, &transform);
    }

    void addRawPath(const RawPath& path) override { path_.addPath(path, nullptr); }

    void fillRule(FillRule value) override { fill_rule_ = value; }

    void rewind() override { path_.rewind(); }

    void moveTo(float x, float y) override { path_.moveTo(x, y); }

    void lineTo(float x, float y) override { path_.lineTo(x, y); }

    void cubicTo(float ox, float oy, float ix, float iy, float x, float y) override { path_.cubic({ox, oy}, {ix, iy}, {x, y}); }

    void close() override { path_.close(); }

    const RawPath& raw_path() const { return path_; }

    FillRule fill_rule() const { return fill_rule_; }

  private:
    RawPath path_;
    FillRule fill_rule_ = FillRule::nonZero;
};

class CairoRenderPaint : public RenderPaint {
  public:
    void style(RenderPaintStyle style) override { style_ = style; }

    void color(ColorInt value) override { color_ = value; }

    void thickness(float value) override { thickness_ = value; }

    void join(StrokeJoin value) override { join_ = value; }

    void cap(StrokeCap value) override { cap_ = value; }

    void feather(float value) override { feather_ = value; }

    void blendMode(BlendMode value) override { blend_mode_ = value; }

    void shader(rive::rcp<RenderShader> shader) override { shader_ = std::move(shader); }

    void invalidateStroke() override {}

    RenderPaintStyle style() const { return style_; }
    ColorInt color() const { return color_; }
    float thickness() const { return thickness_; }
    StrokeJoin join() const { return join_; }
    StrokeCap cap() const { return cap_; }
    float feather() const { return feather_; }
    BlendMode blend_mode() const { return blend_mode_; }
    const rive::rcp<RenderShader>& shader_ref() const { return shader_; }

  private:
    RenderPaintStyle style_ = RenderPaintStyle::fill;
    ColorInt color_ = rive::colorARGB(255, 255, 255, 255);
    float thickness_ = 1.0f;
    StrokeJoin join_ = StrokeJoin::miter;
    StrokeCap cap_ = StrokeCap::butt;
    float feather_ = 0.0f;
    BlendMode blend_mode_ = BlendMode::srcOver;
    rive::rcp<RenderShader> shader_ = nullptr;
};

void append_path(cairo_t* context, const CairoRenderPath& path) {
    cairo_new_path(context);
    cairo_set_fill_rule(context, to_cairo_fill_rule(path.fill_rule()));

    for (const auto [verb, pts] : path.raw_path()) {
        switch (verb) {
            case rive::PathVerb::move:
                cairo_move_to(context, pts[0].x, pts[0].y);
                break;
            case rive::PathVerb::line:
                cairo_line_to(context, pts[1].x, pts[1].y);
                break;
            case rive::PathVerb::quad: {
                const double c1x = pts[0].x + (2.0 / 3.0) * (pts[1].x - pts[0].x);
                const double c1y = pts[0].y + (2.0 / 3.0) * (pts[1].y - pts[0].y);
                const double c2x = pts[2].x + (2.0 / 3.0) * (pts[1].x - pts[2].x);
                const double c2y = pts[2].y + (2.0 / 3.0) * (pts[1].y - pts[2].y);
                cairo_curve_to(context, c1x, c1y, c2x, c2y, pts[2].x, pts[2].y);
                break;
            }
            case rive::PathVerb::cubic:
                cairo_curve_to(context, pts[1].x, pts[1].y, pts[2].x, pts[2].y, pts[3].x, pts[3].y);
                break;
            case rive::PathVerb::close:
                cairo_close_path(context);
                break;
        }
    }
}

class CairoRenderer : public Renderer {
  public:
    explicit CairoRenderer(cairo_t* context, std::string* error) : context_(context), error_(error) {}

    void save() override {
        cairo_save(context_);
        opacity_stack_.push_back(opacity_);
    }

    void restore() override {
        cairo_restore(context_);
        opacity_ = opacity_stack_.empty() ? 1.0 : opacity_stack_.back();
        if (!opacity_stack_.empty()) {
            opacity_stack_.pop_back();
        }
    }

    void transform(const Mat2D& transform) override {
        cairo_matrix_t matrix;
        cairo_matrix_init(&matrix, transform[0], transform[1], transform[2], transform[3], transform[4], transform[5]);
        cairo_transform(context_, &matrix);
    }

    void drawPath(RenderPath* path, RenderPaint* paint) override {
        const auto* cairo_path = static_cast<const CairoRenderPath*>(path);
        const auto* cairo_paint = static_cast<const CairoRenderPaint*>(paint);
        cairo_save(context_);
        cairo_set_operator(context_, to_cairo_operator(cairo_paint->blend_mode()));
        append_path(context_, *cairo_path);
        apply_paint(*cairo_paint);
        if (cairo_paint->style() == RenderPaintStyle::stroke) {
            if (cairo_paint->feather() > 0.0f) {
                cairo_set_line_cap(context_, CAIRO_LINE_CAP_ROUND);
                cairo_set_line_join(context_, CAIRO_LINE_JOIN_ROUND);
            }
            cairo_stroke(context_);
        } else {
            cairo_fill(context_);
        }
        cairo_restore(context_);
    }

    void clipPath(RenderPath* path) override {
        const auto* cairo_path = static_cast<const CairoRenderPath*>(path);
        append_path(context_, *cairo_path);
        cairo_clip(context_);
    }

    void drawImage(const RenderImage*, ImageSampler, BlendMode, float) override {
        unsupported("Rive image rendering is not implemented by the Cairo native backend");
    }

    void drawImageMesh(const RenderImage*,
                       ImageSampler,
                       rive::rcp<rive::RenderBuffer>,
                       rive::rcp<rive::RenderBuffer>,
                       rive::rcp<rive::RenderBuffer>,
                       uint32_t,
                       uint32_t,
                       BlendMode,
                       float) override {
        unsupported("Rive image mesh rendering is not implemented by the Cairo native backend");
    }

    void modulateOpacity(float opacity) override {
        opacity_ *= opacity;
    }

  private:
    void unsupported(const std::string& message) {
        if (error_ != nullptr && error_->empty()) {
            *error_ = message;
        }
    }

    void apply_paint(const CairoRenderPaint& paint) {
        cairo_set_line_width(context_, std::max(0.0f, paint.thickness()));
        cairo_set_line_join(context_, to_cairo_line_join(paint.join()));
        cairo_set_line_cap(context_, to_cairo_line_cap(paint.cap()));

        if (paint.shader_ref() != nullptr) {
            const auto* shader = static_cast<const CairoGradientShader*>(paint.shader_ref().get());
            cairo_pattern_t* pattern = shader->make_pattern(opacity_);
            cairo_set_source(context_, pattern);
            cairo_pattern_destroy(pattern);
            return;
        }

        const auto rgba = unpack_color(paint.color(), opacity_);
        cairo_set_source_rgba(context_, rgba.r, rgba.g, rgba.b, rgba.a);
    }

    cairo_t* context_;
    std::string* error_;
    double opacity_ = 1.0;
    std::vector<double> opacity_stack_;
};

class CairoFactory : public Factory {
  public:
    rive::rcp<rive::RenderBuffer> makeRenderBuffer(rive::RenderBufferType type,
                                                   rive::RenderBufferFlags flags,
                                                   size_t size_in_bytes) override {
        return rive::make_rcp<rive::DataRenderBuffer>(type, flags, size_in_bytes);
    }

    rive::rcp<RenderShader> makeLinearGradient(float sx,
                                               float sy,
                                               float ex,
                                               float ey,
                                               const ColorInt colors[],
                                               const float stops[],
                                               size_t count) override {
        return rive::make_rcp<CairoLinearGradientShader>(sx, sy, ex, ey, colors, stops, count);
    }

    rive::rcp<RenderShader> makeRadialGradient(float cx,
                                               float cy,
                                               float radius,
                                               const ColorInt colors[],
                                               const float stops[],
                                               size_t count) override {
        return rive::make_rcp<CairoRadialGradientShader>(cx, cy, radius, colors, stops, count);
    }

    rive::rcp<RenderPath> makeRenderPath(RawPath& path, FillRule fill_rule) override {
        auto render_path = rive::make_rcp<CairoRenderPath>(std::move(path), fill_rule);
        path.rewind();
        return render_path;
    }

    rive::rcp<RenderPath> makeEmptyRenderPath() override { return rive::make_rcp<CairoRenderPath>(); }

    rive::rcp<RenderPaint> makeRenderPaint() override { return rive::make_rcp<CairoRenderPaint>(); }

    rive::rcp<RenderImage> decodeImage(rive::Span<const uint8_t>) override { return nullptr; }
};

struct RendererSurface {
    RendererSurface(uint32_t width, uint32_t height)
        : width(width), height(height), stride(cairo_format_stride_for_width(CAIRO_FORMAT_ARGB32, static_cast<int>(width))), pixels(stride * height, 0) {
        surface = cairo_image_surface_create_for_data(pixels.data(),
                                                      CAIRO_FORMAT_ARGB32,
                                                      static_cast<int>(width),
                                                      static_cast<int>(height),
                                                      stride);
        if (cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) {
            throw std::runtime_error("failed to create Cairo image surface");
        }
        context = cairo_create(surface);
        if (cairo_status(context) != CAIRO_STATUS_SUCCESS) {
            throw std::runtime_error("failed to create Cairo context");
        }
        cairo_set_antialias(context, CAIRO_ANTIALIAS_BEST);
    }

    ~RendererSurface() {
        if (context != nullptr) {
            cairo_destroy(context);
        }
        if (surface != nullptr) {
            cairo_surface_destroy(surface);
        }
    }

    void clear() {
        cairo_save(context);
        cairo_set_operator(context, CAIRO_OPERATOR_CLEAR);
        cairo_paint(context);
        cairo_restore(context);
    }

    uint32_t width;
    uint32_t height;
    int stride;
    std::vector<unsigned char> pixels;
    cairo_surface_t* surface = nullptr;
    cairo_t* context = nullptr;
};

}  // namespace

struct RiveRendererHandle {
    explicit RiveRendererHandle(uint32_t width, uint32_t height) : surface(width, height) {}

    CairoFactory factory;
    rive::rcp<rive::File> file = nullptr;
    std::unique_ptr<ArtboardInstance> artboard;
    std::unique_ptr<StateMachineInstance> state_machine;
    RendererSurface surface;
};

namespace {

void settle(RiveRendererHandle* handle, float dt_s) {
    handle->state_machine->advanceAndApply(dt_s);
    handle->artboard->advance(dt_s);
}

void render_to_surface(RiveRendererHandle* handle, std::string* error) {
    handle->surface.clear();
    CairoRenderer renderer(handle->surface.context, error);
    renderer.save();
    renderer.align(Fit::contain,
                   Alignment::center,
                   AABB(0, 0, static_cast<float>(handle->surface.width), static_cast<float>(handle->surface.height)),
                   handle->artboard->bounds());
    handle->artboard->draw(&renderer);
    renderer.restore();
    cairo_surface_flush(handle->surface.surface);
}

void copy_surface_to_rgba(const RendererSurface& surface, uint8_t* out_rgba) {
    for (uint32_t y = 0; y < surface.height; ++y) {
        const auto* row = surface.pixels.data() + y * surface.stride;
        auto* out_row = out_rgba + (static_cast<size_t>(y) * surface.width * 4);
        for (uint32_t x = 0; x < surface.width; ++x) {
            const uint8_t b = row[x * 4 + 0];
            const uint8_t g = row[x * 4 + 1];
            const uint8_t r = row[x * 4 + 2];
            const uint8_t a = row[x * 4 + 3];

            if (a == 0) {
                out_row[x * 4 + 0] = 0;
                out_row[x * 4 + 1] = 0;
                out_row[x * 4 + 2] = 0;
                out_row[x * 4 + 3] = 0;
                continue;
            }

            const auto unpremul = [a](uint8_t channel) -> uint8_t {
                if (a == 255) {
                    return channel;
                }
                const double value = (static_cast<double>(channel) * 255.0) / static_cast<double>(a);
                return static_cast<uint8_t>(std::clamp(std::lround(value), 0L, 255L));
            };

            out_row[x * 4 + 0] = unpremul(r);
            out_row[x * 4 + 1] = unpremul(g);
            out_row[x * 4 + 2] = unpremul(b);
            out_row[x * 4 + 3] = a;
        }
    }
}

template <typename Callback>
bool with_errors(char** error_out, Callback&& callback) {
    if (error_out != nullptr) {
        *error_out = nullptr;
    }
    try {
        callback();
        return true;
    } catch (const std::exception& exception) {
        set_error(error_out, exception.what());
        return false;
    } catch (...) {
        set_error(error_out, "unknown native Rive error");
        return false;
    }
}

}  // namespace

extern "C" {

RiveRendererHandle* rive_renderer_create(const char* riv_path,
                                         const char* artboard,
                                         const char* state_machine,
                                         uint32_t width,
                                         uint32_t height,
                                         char** error_out) {
    RiveRendererHandle* handle = nullptr;
    const bool ok = with_errors(error_out, [&]() {
        ensure_name(artboard, "artboard");
        ensure_name(state_machine, "state_machine");
        if (width == 0 || height == 0) {
            throw std::runtime_error("width and height must be positive");
        }

        auto bytes = read_file_bytes(riv_path);
        auto candidate = std::make_unique<RiveRendererHandle>(width, height);
        candidate->file = rive::File::import(bytes, &candidate->factory);
        if (candidate->file == nullptr) {
            throw std::runtime_error("failed to import Rive asset through the native runtime");
        }

        candidate->artboard = candidate->file->artboardNamed(artboard);
        if (!candidate->artboard) {
            throw std::runtime_error(std::string("artboard '") + artboard + "' was not found");
        }

        candidate->state_machine = candidate->artboard->stateMachineNamed(state_machine);
        if (!candidate->state_machine) {
            throw std::runtime_error(std::string("state machine '") + state_machine + "' was not found on artboard '" + artboard + "'");
        }

        settle(candidate.get(), 0.0f);
        handle = candidate.release();
    });

    if (!ok) {
        delete handle;
        return nullptr;
    }
    return handle;
}

void rive_renderer_destroy(RiveRendererHandle* handle) { delete handle; }

bool rive_renderer_set_bool(RiveRendererHandle* handle,
                            const char* input_name,
                            bool value,
                            char** error_out) {
    return with_errors(error_out, [&]() {
        if (handle == nullptr) {
            throw std::runtime_error("Rive renderer handle is null");
        }
        ensure_name(input_name, "input_name");
        SMIBool* input = handle->state_machine->getBool(input_name);
        if (input == nullptr) {
            throw std::runtime_error(std::string("state machine bool input '") + input_name + "' was not found");
        }
        input->value(value);
    });
}

bool rive_renderer_set_number(RiveRendererHandle* handle,
                              const char* input_name,
                              float value,
                              char** error_out) {
    return with_errors(error_out, [&]() {
        if (handle == nullptr) {
            throw std::runtime_error("Rive renderer handle is null");
        }
        ensure_name(input_name, "input_name");
        if (!std::isfinite(value)) {
            throw std::runtime_error("number input values must be finite");
        }
        SMINumber* input = handle->state_machine->getNumber(input_name);
        if (input == nullptr) {
            throw std::runtime_error(std::string("state machine number input '") + input_name + "' was not found");
        }
        input->value(value);
    });
}

bool rive_renderer_fire_trigger(RiveRendererHandle* handle,
                                const char* input_name,
                                char** error_out) {
    return with_errors(error_out, [&]() {
        if (handle == nullptr) {
            throw std::runtime_error("Rive renderer handle is null");
        }
        ensure_name(input_name, "input_name");
        SMITrigger* input = handle->state_machine->getTrigger(input_name);
        if (input == nullptr) {
            throw std::runtime_error(std::string("state machine trigger input '") + input_name + "' was not found");
        }
        input->fire();
    });
}

bool rive_renderer_advance(RiveRendererHandle* handle, float dt_s, char** error_out) {
    return with_errors(error_out, [&]() {
        if (handle == nullptr) {
            throw std::runtime_error("Rive renderer handle is null");
        }
        if (!std::isfinite(dt_s) || dt_s < 0.0f) {
            throw std::runtime_error("dt_s must be a finite, non-negative number");
        }
        settle(handle, dt_s);
    });
}

bool rive_renderer_render_rgba(RiveRendererHandle* handle,
                               uint8_t* out_rgba,
                               uint32_t out_len,
                               char** error_out) {
    return with_errors(error_out, [&]() {
        if (handle == nullptr) {
            throw std::runtime_error("Rive renderer handle is null");
        }
        if (out_rgba == nullptr) {
            throw std::runtime_error("out_rgba must not be null");
        }
        const uint32_t expected_len = handle->surface.width * handle->surface.height * 4;
        if (out_len != expected_len) {
            throw std::runtime_error("output RGBA buffer size does not match renderer dimensions");
        }

        settle(handle, 0.0f);
        std::string render_error;
        render_to_surface(handle, &render_error);
        if (!render_error.empty()) {
            throw std::runtime_error(render_error);
        }
        copy_surface_to_rgba(handle->surface, out_rgba);
    });
}

void rive_renderer_free_error(char* error) { std::free(error); }

}

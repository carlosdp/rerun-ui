from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Any, Sequence

import numpy as np

from ._manager import spawn_viewer

LOGGER = logging.getLogger(__name__)
_TEXTURE_COMPONENTS = frozenset({"Mesh3D:albedo_texture_buffer", "Mesh3D:albedo_texture_format"})


@dataclass(frozen=True)
class FaceQuad:
    entity_path: str
    parent_entity: str
    center_xyz: tuple[float, float, float]
    u_axis_xyz: tuple[float, float, float]
    v_axis_xyz: tuple[float, float, float]
    size_m: tuple[float, float]
    z_offset_m: float = 0.001


@dataclass(frozen=True)
class FacePatchMesh:
    entity_path: str
    parent_entity: str
    vertices_xyz: Sequence[tuple[float, float, float]]
    triangle_indices: Sequence[tuple[int, int, int]]
    uvs: Sequence[tuple[float, float]]


@dataclass(frozen=True)
class _MeshDefinition:
    entity_path: str
    vertex_positions: tuple[tuple[float, float, float], ...]
    triangle_indices: tuple[tuple[int, int, int], ...]
    vertex_texcoords: tuple[tuple[float, float], ...]
    translation: tuple[float, float, float] | None = None


class FaceSurface:
    def __init__(
        self,
        target: FaceQuad | FacePatchMesh,
        *,
        recording: Any | None = None,
    ) -> None:
        self._recording = _resolve_recording(recording)
        self._target = target
        self._mesh = _build_mesh_definition(target)
        self._closed = False
        self._log_static_geometry()

    @property
    def entity_path(self) -> str:
        return self._mesh.entity_path

    def update_rgba(
        self,
        rgba: bytes | bytearray | memoryview,
        width: int,
        height: int,
        *,
        sim_time_s: float | None = None,
    ) -> None:
        self._ensure_open()
        frame = _rgba_frame(rgba, width, height)
        if sim_time_s is not None:
            _set_sim_time(self._recording, sim_time_s)
        self._recording.log(self.entity_path, *_texture_batches(self._mesh, frame))

    def close(self) -> None:
        if self._closed:
            return
        rr = _require_rerun()
        self._recording.log(self.entity_path, rr.Clear(recursive=True))
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("face surface is closed")

    def _log_static_geometry(self) -> None:
        rr = _require_rerun()
        if self._mesh.translation is not None:
            self._recording.log(
                self.entity_path,
                rr.Transform3D(translation=self._mesh.translation),
                static=True,
            )

        self._recording.log(self.entity_path, _build_mesh_archetype(self._mesh), static=True)


def create_face_surface(
    target: FaceQuad | FacePatchMesh,
    *,
    recording: Any | None = None,
) -> FaceSurface:
    return FaceSurface(target, recording=recording)


def _resolve_recording(recording: Any | None) -> Any:
    if recording is not None:
        return recording

    rr = _require_rerun()
    active_recording = rr.get_global_data_recording()
    if active_recording is None:
        try:
            spawn_viewer(connect_sdk=True)
        except Exception:
            LOGGER.debug("Failed to auto-connect Rerun viewer for face surface", exc_info=True)
        active_recording = rr.get_global_data_recording()

    return active_recording if active_recording is not None else rr


def _require_rerun() -> Any:
    try:
        import rerun as rr
    except Exception as exc:  # pragma: no cover - dependency is required at runtime
        raise RuntimeError("rerun package is required for face surface logging") from exc
    return rr


def _set_sim_time(recording: Any, sim_time_s: float) -> None:
    sim_time = float(sim_time_s)
    if not math.isfinite(sim_time):
        raise ValueError("sim_time_s must be finite")

    set_time = getattr(recording, "set_time", None)
    if callable(set_time):
        set_time("sim_time", duration=sim_time)
        return

    rr = _require_rerun()
    if recording is rr:
        rr.set_time("sim_time", duration=sim_time)
    else:
        rr.set_time("sim_time", duration=sim_time, recording=recording)


def _rgba_frame(rgba: bytes | bytearray | memoryview, width: int, height: int) -> np.ndarray[Any, np.dtype[np.uint8]]:
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    try:
        view = memoryview(rgba)
    except TypeError as exc:
        raise TypeError("rgba must support the buffer protocol") from exc

    if view.ndim != 1:
        view = view.cast("B")

    expected_size = width * height * 4
    if len(view) != expected_size:
        raise ValueError(
            f"RGBA buffer length must be width * height * 4 bytes; expected {expected_size}, got {len(view)}"
        )

    return np.frombuffer(view, dtype=np.uint8, count=expected_size).reshape((height, width, 4))


def _build_mesh_definition(target: FaceQuad | FacePatchMesh) -> _MeshDefinition:
    if isinstance(target, FaceQuad):
        return _build_quad_mesh_definition(target)
    return _build_patch_mesh_definition(target)


def _build_quad_mesh_definition(target: FaceQuad) -> _MeshDefinition:
    entity_path = _validate_entity_hierarchy(target.entity_path, target.parent_entity)
    center = _as_vec3(target.center_xyz, field_name="center_xyz")
    u_axis = _normalized(_as_vec3(target.u_axis_xyz, field_name="u_axis_xyz"), field_name="u_axis_xyz")
    v_axis = _normalized(_as_vec3(target.v_axis_xyz, field_name="v_axis_xyz"), field_name="v_axis_xyz")

    width_m, height_m = _as_size_pair(target.size_m)
    normal = _normalized(_cross(u_axis, v_axis), field_name="u_axis_xyz x v_axis_xyz")
    translation = _add(center, _scale(normal, float(target.z_offset_m)))
    half_u = _scale(u_axis, width_m / 2.0)
    half_v = _scale(v_axis, height_m / 2.0)

    return _MeshDefinition(
        entity_path=entity_path,
        translation=translation,
        vertex_positions=(
            _add(_scale(half_u, -1.0), _scale(half_v, -1.0)),
            _add(half_u, _scale(half_v, -1.0)),
            _add(half_u, half_v),
            _add(_scale(half_u, -1.0), half_v),
        ),
        triangle_indices=((0, 1, 2), (0, 2, 3)),
        vertex_texcoords=((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)),
    )


def _build_patch_mesh_definition(target: FacePatchMesh) -> _MeshDefinition:
    entity_path = _validate_entity_hierarchy(target.entity_path, target.parent_entity)
    vertex_positions = tuple(_as_vec3(vertex, field_name="vertices_xyz") for vertex in target.vertices_xyz)
    if len(vertex_positions) < 3:
        raise ValueError("vertices_xyz must contain at least three vertices")

    vertex_texcoords = tuple(_as_vec2(uv, field_name="uvs") for uv in target.uvs)
    if len(vertex_texcoords) != len(vertex_positions):
        raise ValueError("uvs must contain one entry per vertex")

    triangle_indices = tuple(_as_triangle(indices) for indices in target.triangle_indices)
    if not triangle_indices:
        raise ValueError("triangle_indices must contain at least one triangle")

    vertex_count = len(vertex_positions)
    for triangle in triangle_indices:
        for index in triangle:
            if index < 0 or index >= vertex_count:
                raise ValueError("triangle_indices contain an out-of-range vertex index")

    return _MeshDefinition(
        entity_path=entity_path,
        vertex_positions=vertex_positions,
        triangle_indices=triangle_indices,
        vertex_texcoords=vertex_texcoords,
    )


def _build_mesh_archetype(
    mesh: _MeshDefinition,
    texture: np.ndarray[Any, np.dtype[np.uint8]] | None = None,
) -> Any:
    rr = _require_rerun()
    return rr.Mesh3D(
        vertex_positions=mesh.vertex_positions,
        triangle_indices=mesh.triangle_indices,
        vertex_texcoords=mesh.vertex_texcoords,
        albedo_texture=texture,
    )


def _texture_batches(
    mesh: _MeshDefinition,
    texture: np.ndarray[Any, np.dtype[np.uint8]],
) -> tuple[Any, ...]:
    batches = tuple(
        batch
        for batch in _build_mesh_archetype(mesh, texture).as_component_batches()
        if str(batch.component_descriptor()) in _TEXTURE_COMPONENTS
    )
    if not batches:
        raise RuntimeError("failed to build texture component batches for face surface")
    return batches


def _validate_entity_hierarchy(entity_path: str, parent_entity: str) -> str:
    entity = _normalize_entity_path(entity_path, field_name="entity_path")
    parent = _normalize_entity_path(parent_entity, field_name="parent_entity")
    parent_prefix = "/" if parent == "/" else f"{parent}/"
    if entity == parent or not entity.startswith(parent_prefix):
        raise ValueError("entity_path must be nested under parent_entity")
    return entity


def _normalize_entity_path(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    path = value.strip()
    if not path:
        raise ValueError(f"{field_name} must not be empty")
    if not path.startswith("/"):
        raise ValueError(f"{field_name} must start with '/'")
    if path != "/":
        path = path.rstrip("/")
    return path


def _as_size_pair(raw: tuple[float, float]) -> tuple[float, float]:
    if len(raw) != 2:
        raise ValueError("size_m must contain exactly two values")
    width = float(raw[0])
    height = float(raw[1])
    if width <= 0.0 or height <= 0.0:
        raise ValueError("size_m values must be positive")
    return width, height


def _as_vec3(raw: Sequence[float], *, field_name: str) -> tuple[float, float, float]:
    if len(raw) != 3:
        raise ValueError(f"{field_name} must contain exactly three values")
    return float(raw[0]), float(raw[1]), float(raw[2])


def _as_vec2(raw: Sequence[float], *, field_name: str) -> tuple[float, float]:
    if len(raw) != 2:
        raise ValueError(f"{field_name} must contain exactly two values")
    return float(raw[0]), float(raw[1])


def _as_triangle(raw: Sequence[int]) -> tuple[int, int, int]:
    if len(raw) != 3:
        raise ValueError("triangle_indices entries must contain exactly three indices")
    return int(raw[0]), int(raw[1]), int(raw[2])


def _normalized(vector: tuple[float, float, float], *, field_name: str) -> tuple[float, float, float]:
    length = math.sqrt(sum(component * component for component in vector))
    if length <= 0.0:
        raise ValueError(f"{field_name} must be non-zero")
    return tuple(component / length for component in vector)  # type: ignore[return-value]


def _cross(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        lhs[1] * rhs[2] - lhs[2] * rhs[1],
        lhs[2] * rhs[0] - lhs[0] * rhs[2],
        lhs[0] * rhs[1] - lhs[1] * rhs[0],
    )


def _scale(vector: tuple[float, float, float], scalar: float) -> tuple[float, float, float]:
    return tuple(component * scalar for component in vector)  # type: ignore[return-value]


def _add(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(lhs[index] + rhs[index] for index in range(3))  # type: ignore[return-value]

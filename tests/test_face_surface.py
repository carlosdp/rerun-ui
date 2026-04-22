from __future__ import annotations

import pytest
import rerun as rr

from rerun_ui import FacePatchMesh, FaceQuad, FaceSurface, create_face_surface


class RecordingSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.time_calls: list[tuple[str, dict[str, object]]] = []

    def log(self, entity_path: str, *components: object, **kwargs: object) -> None:
        self.calls.append((entity_path, components, kwargs))

    def set_time(self, timeline: str, **kwargs: object) -> None:
        self.time_calls.append((timeline, kwargs))


def _component_kinds(components: tuple[object, ...]) -> list[str]:
    kinds: list[str] = []
    for component in components:
        descriptor = getattr(component, "component_descriptor", None)
        if callable(descriptor):
            kinds.append(str(descriptor()))
        else:
            kinds.append(type(component).__name__)
    return kinds


def _make_quad() -> FaceQuad:
    return FaceQuad(
        entity_path="/robot/head/face",
        parent_entity="/robot/head",
        center_xyz=(0.0, 0.0, 0.0),
        u_axis_xyz=(1.0, 0.0, 0.0),
        v_axis_xyz=(0.0, 1.0, 0.0),
        size_m=(0.2, 0.1),
    )


def _make_patch_mesh() -> FacePatchMesh:
    return FacePatchMesh(
        entity_path="/robot/head/face_patch",
        parent_entity="/robot/head",
        vertices_xyz=(
            (-0.1, -0.05, 0.0),
            (0.1, -0.05, 0.0),
            (0.1, 0.05, 0.0),
            (-0.1, 0.05, 0.0),
        ),
        triangle_indices=((0, 1, 2), (0, 2, 3)),
        uvs=((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)),
    )


def test_face_surface_public_api_exports() -> None:
    assert FaceQuad is not None
    assert FacePatchMesh is not None
    assert FaceSurface is not None
    assert create_face_surface is not None


def test_face_surface_logs_quad_image_updates() -> None:
    recording = RecordingSpy()
    surface = create_face_surface(_make_quad(), recording=recording)

    assert isinstance(surface, FaceSurface)
    assert len(recording.calls) == 2
    assert recording.calls[0][0] == "/robot/head/face"
    assert isinstance(recording.calls[0][1][0], rr.Transform3D)
    assert recording.calls[0][2] == {"static": True}
    assert recording.calls[1][0] == "/robot/head/face"
    assert isinstance(recording.calls[1][1][0], rr.Mesh3D)
    assert recording.calls[1][2] == {"static": True}

    surface.update_rgba(bytes([255, 0, 0, 255] * 4), 2, 2, sim_time_s=1.5)

    assert recording.time_calls == [("sim_time", {"duration": 1.5})]
    assert len(recording.calls) == 3
    assert recording.calls[2][0] == "/robot/head/face"
    assert _component_kinds(recording.calls[2][1]) == [
        "Mesh3D:albedo_texture_buffer",
        "Mesh3D:albedo_texture_format",
    ]
    assert recording.calls[2][2] == {}


def test_face_surface_reuses_stable_entity_path_across_updates() -> None:
    recording = RecordingSpy()
    surface = create_face_surface(_make_quad(), recording=recording)

    surface.update_rgba(bytes([255, 0, 0, 255] * 4), 2, 2)
    surface.update_rgba(bytes([0, 255, 0, 255] * 4), 2, 2)

    assert [call[0] for call in recording.calls] == [
        "/robot/head/face",
        "/robot/head/face",
        "/robot/head/face",
        "/robot/head/face",
    ]
    assert sum(1 for _path, _components, kwargs in recording.calls if kwargs.get("static")) == 2


@pytest.mark.parametrize(
    ("rgba", "width", "height", "message"),
    [
        (bytes([255, 0, 0, 255] * 3), 2, 2, "RGBA buffer length"),
        (bytes([255, 0, 0, 255] * 4), 0, 2, "width and height must be positive"),
        (bytes([255, 0, 0, 255] * 4), 2, -1, "width and height must be positive"),
    ],
)
def test_face_surface_rejects_invalid_frame_shapes(
    rgba: bytes,
    width: int,
    height: int,
    message: str,
) -> None:
    recording = RecordingSpy()
    surface = create_face_surface(_make_quad(), recording=recording)

    with pytest.raises(ValueError, match=message):
        surface.update_rgba(rgba, width, height)

    assert len(recording.calls) == 2


def test_face_surface_close_is_idempotent() -> None:
    recording = RecordingSpy()
    surface = create_face_surface(_make_quad(), recording=recording)

    surface.close()
    surface.close()

    clear_calls = [call for call in recording.calls if isinstance(call[1][0], rr.Clear)]
    assert len(clear_calls) == 1
    assert clear_calls[0][0] == "/robot/head/face"

    with pytest.raises(RuntimeError, match="closed"):
        surface.update_rgba(bytes([255, 0, 0, 255] * 4), 2, 2)


def test_face_patch_mesh_logs_static_topology_once() -> None:
    recording = RecordingSpy()
    surface = create_face_surface(_make_patch_mesh(), recording=recording)

    assert len(recording.calls) == 1
    assert recording.calls[0][0] == "/robot/head/face_patch"
    assert isinstance(recording.calls[0][1][0], rr.Mesh3D)
    assert recording.calls[0][2] == {"static": True}

    surface.update_rgba(bytes([255, 255, 255, 255] * 4), 2, 2)
    surface.update_rgba(bytes([0, 0, 0, 255] * 4), 2, 2)

    assert sum(1 for _path, _components, kwargs in recording.calls if kwargs.get("static")) == 1
    assert [_component_kinds(call[1]) for call in recording.calls[1:]] == [
        ["Mesh3D:albedo_texture_buffer", "Mesh3D:albedo_texture_format"],
        ["Mesh3D:albedo_texture_buffer", "Mesh3D:albedo_texture_format"],
    ]


def test_face_patch_mesh_requires_matching_uvs() -> None:
    target = FacePatchMesh(
        entity_path="/robot/head/face_patch",
        parent_entity="/robot/head",
        vertices_xyz=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        triangle_indices=((0, 1, 2),),
        uvs=((0.0, 0.0), (1.0, 0.0)),
    )

    with pytest.raises(ValueError, match="uvs"):
        create_face_surface(target, recording=RecordingSpy())

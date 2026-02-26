from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ViewerStatus(str, Enum):
    CUSTOM_CONNECTED = "custom_connected"
    PLAIN_CONNECTED = "plain_connected"
    DISCONNECTED = "disconnected"


class PointerEventType(str, Enum):
    PRESS = "press"
    RELEASE = "release"
    CLICK = "click"
    DRAG = "drag"


class PointerButton(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    MIDDLE = "middle"


@dataclass(frozen=True)
class Pointer3DEvent:
    event_type: PointerEventType
    button: PointerButton
    view_id: str
    space_origin: str
    pointer_ui: tuple[float, float]
    pointer_view: tuple[float, float]
    ray_origin: tuple[float, float, float]
    ray_direction: tuple[float, float, float]
    projected_position: tuple[float, float, float] | None
    drag_delta: tuple[float, float] | None


class Key(str, Enum):
    ARROW_DOWN = "ARROW_DOWN"
    ARROW_LEFT = "ARROW_LEFT"
    ARROW_RIGHT = "ARROW_RIGHT"
    ARROW_UP = "ARROW_UP"
    ESCAPE = "ESCAPE"
    TAB = "TAB"
    BACKSPACE = "BACKSPACE"
    ENTER = "ENTER"
    SPACE = "SPACE"
    INSERT = "INSERT"
    DELETE = "DELETE"
    HOME = "HOME"
    END = "END"
    PAGE_UP = "PAGE_UP"
    PAGE_DOWN = "PAGE_DOWN"

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"
    I = "I"
    J = "J"
    K = "K"
    L = "L"
    M = "M"
    N = "N"
    O = "O"
    P = "P"
    Q = "Q"
    R = "R"
    S = "S"
    T = "T"
    U = "U"
    V = "V"
    W = "W"
    X = "X"
    Y = "Y"
    Z = "Z"

    NUM0 = "NUM0"
    NUM1 = "NUM1"
    NUM2 = "NUM2"
    NUM3 = "NUM3"
    NUM4 = "NUM4"
    NUM5 = "NUM5"
    NUM6 = "NUM6"
    NUM7 = "NUM7"
    NUM8 = "NUM8"
    NUM9 = "NUM9"

    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    F4 = "F4"
    F5 = "F5"
    F6 = "F6"
    F7 = "F7"
    F8 = "F8"
    F9 = "F9"
    F10 = "F10"
    F11 = "F11"
    F12 = "F12"

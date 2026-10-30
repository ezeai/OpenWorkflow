"""A union type such as FLOAT,INT is still a number widget, so saved widget values keep their place."""
from openfield.forge.object_info import _parse_input


def test_union_of_number_types_is_a_widget():
    spec = _parse_input("frame_rate", ("FLOAT,INT", {"default": 25.0, "min": 1.0}), True)
    assert spec.is_widget


def test_socket_union_is_not_a_widget():
    spec = _parse_input("image", ("IMAGE,MASK", {}), True)
    assert not spec.is_widget


def test_plain_widget_still_a_widget():
    spec = _parse_input("frames_number", ("INT", {"default": 97}), True)
    assert spec.is_widget

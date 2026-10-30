"""Nav tabs: validation, saving and reading the saved list."""
import pytest
from pydantic import ValidationError

from openfield.forge import navtabs

A = "a" * 32
B = "b" * 32


def test_label_is_trimmed_and_required():
    assert navtabs.NavTab(app_id=A, label="  Pika  ").label == "Pika"
    with pytest.raises(ValidationError):
        navtabs.NavTab(app_id=A, label="   ")


def test_app_id_must_be_a_forge_id():
    with pytest.raises(ValidationError):
        navtabs.NavTab(app_id="not-an-id", label="x")


def test_an_app_can_only_have_one_tab():
    with pytest.raises(ValidationError):
        navtabs.NavTabsBody(tabs=[{"app_id": A, "label": "one"}, {"app_id": A, "label": "two"}])


def test_save_then_load_keeps_order_and_labels(tmp_path):
    path = tmp_path / "nav_tabs.json"
    body = navtabs.NavTabsBody(tabs=[{"app_id": B, "label": "Second"}, {"app_id": A, "label": "First"}])
    saved = navtabs.save(path, body)
    assert [t["label"] for t in saved] == ["Second", "First"]
    assert [t["label"] for t in navtabs.load(path)] == ["Second", "First"]


def test_missing_or_broken_file_means_no_tabs(tmp_path):
    assert navtabs.load(tmp_path / "missing.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert navtabs.load(broken) == []

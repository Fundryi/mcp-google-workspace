"""Tests for scripts/mcp_file_call.py (no network)."""

import base64
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mcp_file_call",
    Path(__file__).resolve().parent.parent / "scripts" / "mcp_file_call.py",
)
mfc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mfc)


def test_swap_files_replaces_marker_with_standard_base64(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"\xfb\xff\x00hello")
    warnings = []
    args = {"attachments": [{"content": f"@file:{f}", "filename": "a.bin"}], "x": 1}
    out, total = mfc.swap_files(args, warnings)
    assert out["attachments"][0]["content"] == base64.b64encode(f.read_bytes()).decode()
    assert (
        "+" in out["attachments"][0]["content"]
        or "/" in out["attachments"][0]["content"]
    )
    assert out["x"] == 1
    assert total == f.stat().st_size
    assert warnings == []


def test_swap_files_warns_without_name_and_fails_on_missing_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"data")
    warnings = []
    mfc.swap_files({"attachments": [{"content": f"@file:{f}"}]}, warnings)
    assert len(warnings) == 1 and "a.bin" in warnings[0]
    with pytest.raises(mfc.UsageError, match="not a readable file"):
        mfc.swap_files({"content": f"@file:{tmp_path / 'missing'}"}, [])
    with pytest.raises(mfc.UsageError):
        mfc.swap_files({"content": f"@file:{tmp_path}"}, [])


def test_strip_base64_removes_long_runs_only():
    blob = base64.b64encode(b"x" * 3000).decode()
    text = f"Draft created! Draft ID: r123\n{blob}\nshort AAAA"
    out = mfc.strip_base64(text)
    assert blob not in out
    assert "[base64, 3000 bytes removed]" in out
    assert "Draft ID: r123" in out and "short AAAA" in out


def test_extract_one_base64_from_fastmail_and_gmail_shapes():
    data = bytes(range(256)) * 10
    b64 = base64.b64encode(data).decode()
    fastmail = json.dumps(
        {"name": "f.pdf", "type": "application/pdf", "size": len(data), "base64": b64}
    )
    gmail = f"Attachment downloaded\n📦 Base64 content ({len(b64)} chars):\n{b64}"
    assert mfc.extract_one_base64([fastmail]) == data
    assert mfc.extract_one_base64([gmail, gmail]) == data
    with pytest.raises(mfc.UsageError, match="found 0"):
        mfc.extract_one_base64(["no file here"])
    other = base64.b64encode(b"y" * 2000).decode()
    with pytest.raises(mfc.UsageError, match="found 2"):
        mfc.extract_one_base64([b64, other])


def test_result_texts_ignores_structured_content():
    reply = {
        "content": [{"type": "text", "text": "hello"}],
        "structuredContent": {"result": "hello"},
        "isError": False,
    }
    assert mfc.result_texts(reply) == (False, ["hello"])
    assert mfc.result_texts({"success": False, "message": "nope"}) == (True, ["nope"])


def test_main_refuses_oversized_request_before_sending(tmp_path, monkeypatch, capsys):
    f = tmp_path / "big.bin"
    f.write_bytes(b"z" * 3000)
    args = tmp_path / "args.json"
    args.write_text(json.dumps({"content": f"@file:{f}", "name": "big.bin"}))
    monkeypatch.setenv("MCPHUB_URL", "https://hub.example.com/mcp")
    monkeypatch.setenv("MCPHUB_KEY", "secret-value")

    def no_network(*a, **k):
        raise AssertionError("must not send")

    monkeypatch.setattr(mfc.urllib.request, "urlopen", no_network)
    code = mfc.main(["fastmail", "create_draft", str(args), "--max-body", "1000"])
    err = capsys.readouterr().err
    assert code == 2
    assert "hub accepts 1000" in err
    assert "secret-value" not in err


def test_hub_settings_from_env_adds_bearer(monkeypatch):
    monkeypatch.setenv("MCPHUB_URL", "https://hub.example.com/mcp/$smart")
    monkeypatch.setenv("MCPHUB_KEY", "abc")
    assert mfc.hub_settings() == ("https://hub.example.com", "Bearer abc")

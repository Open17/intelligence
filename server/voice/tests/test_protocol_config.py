import pytest

from intelligence import protocol
from intelligence.config import Settings


def test_encode_decode_roundtrip() -> None:
    msg = protocol.decode(protocol.encode("audio_start", id=3, sample_rate=22050))
    assert msg == {"type": "audio_start", "id": 3, "sample_rate": 22050}


@pytest.mark.parametrize("bad", ["not json", "[1,2]", '{"no_type":1}', '{"type":5}', "x" * 5000])
def test_decode_rejects_bad_frames(bad: str) -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.decode(bad)


def test_settings_from_env(monkeypatch, tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("s3cret\n")
    monkeypatch.setenv("INTEL_AGENT_TOKEN_FILE", str(token))
    monkeypatch.setenv("INTEL_FOLLOWUP_SECONDS", "12.5")
    monkeypatch.setenv("INTEL_BUILD_EFFORT", "max")
    monkeypatch.setenv("INTEL_ALLOWED_DEVICES", "pi, kitchen-pi")
    s = Settings.from_env()
    assert s.agent_token == "s3cret"
    assert s.followup_seconds == 12.5
    assert s.build.model == "claude-opus-5-5" and s.build.effort == "max"
    assert s.voice.model == "claude-haiku-5-5" and s.voice.effort == "medium"
    assert s.allowed_devices == ("pi", "kitchen-pi")
    assert "s3cret" not in repr(s)

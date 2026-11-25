"""``python -m intelligence_client [--list-devices | --test-audio]``"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

from . import __version__
from .config import DEFAULT_ENV_FILE, ClientSettings


def list_devices() -> None:
    import sounddevice as sd

    print(sd.query_devices())
    print("\nUse the number (or part of the name) as INTEL_INPUT_DEVICE / INTEL_OUTPUT_DEVICE.")


def test_audio(settings: ClientSettings) -> None:
    """Plays the wake chime, records 3 s, plays it back: checks both devices."""
    import numpy as np
    import sounddevice as sd

    from .audio import resolve_device
    from .dsp import earcons

    out_dev = resolve_device(settings.output_device, "output")
    in_dev = resolve_device(settings.input_device, "input")
    rate = int(sd.query_devices(out_dev, "output")["default_samplerate"])
    print("Playing a chime...")
    sd.play(earcons(rate)["wake"], rate, device=out_dev, blocking=True)
    in_rate = int(sd.query_devices(in_dev, "input")["default_samplerate"])
    print("Recording 3 seconds: say something!")
    rec = sd.rec(int(3 * in_rate), samplerate=in_rate, channels=1, dtype="float32", device=in_dev, blocking=True)
    level = float(np.sqrt(np.mean(rec**2)))
    print(f"Input level (RMS): {level:.4f}  {'OK' if level > 0.003 else 'very quiet: check the microphone'}")
    print("Playing it back...")
    sd.play(rec[:, 0], in_rate, device=out_dev, blocking=True)
    time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser(prog="intelligence-client", description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--list-devices", action="store_true", help="show audio devices and exit")
    parser.add_argument("--test-audio", action="store_true", help="test speaker and microphone and exit")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    settings = ClientSettings.load(args.config)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)

    if args.list_devices:
        list_devices()
        return
    if args.test_audio:
        test_audio(settings)
        return

    from .app import run

    async def main_async() -> None:
        task = asyncio.create_task(run(settings))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(main_async())
    except ValueError as e:  # e.g. no such audio device
        sys.exit(f"intelligence-client: {e}")


if __name__ == "__main__":
    main()

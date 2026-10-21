"""Payload que permanece vivo para exercitar a remoção após timeout."""

from pathlib import Path
import time


Path("/work/hang-started").write_text("iniciado\n", encoding="utf-8")
while True:
    time.sleep(60)

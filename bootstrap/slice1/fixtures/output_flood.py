"""Payload de saída ilimitada para exercitar o limite do coletor."""

import os


chunk = b"fixture-output-flood\n" * 4096
while True:
    os.write(1, chunk)

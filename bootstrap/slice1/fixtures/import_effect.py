"""Efeito de topo permitido somente na execução explícita no contêiner."""

from pathlib import Path


Path("/work/import-effect").write_text("executado no contêiner\n", encoding="utf-8")
print("IMPORT_EFFECT_EXECUTED", flush=True)

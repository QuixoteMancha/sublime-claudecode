#!/usr/bin/env python3
"""Hook PreToolUse de Claude Code: copia un archivo antes de que Claude lo edite.

Uso: snapshot.py <carpeta de respaldo>. El evento llega en JSON por stdin.
Con esas copias el plugin muestra el diff de cada edición y puede revertirla.
Nunca bloquea la herramienta: ante cualquier problema termina en silencio.
"""
import json
import os
import shutil
import sys


def main():
    backup = sys.argv[1]
    event = json.load(sys.stdin)
    tool_input = event.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return
    path = os.path.abspath(os.path.join(event.get("cwd") or os.getcwd(), path))
    os.makedirs(backup, exist_ok=True)
    manifest = os.path.join(backup, "manifest.jsonl")
    seen = []
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as handle:
            seen = [json.loads(line)["path"] for line in handle if line.strip()]
    if path in seen:
        # Sólo interesa cómo estaba antes de la primera edición.
        return
    entry = {"path": path, "existed": os.path.isfile(path), "copy": str(len(seen))}
    if entry["existed"]:
        shutil.copy2(path, os.path.join(backup, entry["copy"]))
    with open(manifest, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

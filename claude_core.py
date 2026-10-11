"""Lógica del plugin que no depende de Sublime Text.

Vive aparte para poder probarla con un Python cualquiera (ver tests/).
Tiene que seguir siendo compatible con Python 3.8, que es el del host de plugins.
"""
import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading

READ_TOOLS = "Read,Grep,Glob"
EDIT_TOOLS = "Read,Edit,Write,Grep,Glob"
NO_TOOLS = ""

TERMINALS = (
    "x-terminal-emulator",
    "gnome-terminal",
    "mate-terminal",
    "konsole",
    "xfce4-terminal",
    "kitty",
    "alacritty",
    "xterm",
)


def search_path():
    """PATH ampliado: Sublime abierto desde el menú del escritorio no hereda ~/.local/bin."""
    extra = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.claude/local"),
        "/usr/local/bin",
    ]
    return os.pathsep.join([os.environ.get("PATH", "")] + extra)


def find_claude(configured=None):
    return shutil.which(os.path.expanduser(configured or "claude"), path=search_path())


def find_python():
    """Un Python del sistema para correr el hook (el de Sublime no es un ejecutable)."""
    return shutil.which("python3", path=search_path())


def find_terminal(configured=None):
    if configured:
        return shutil.which(os.path.expanduser(configured), path=search_path())
    for name in TERMINALS:
        found = shutil.which(name, path=search_path())
        if found:
            return found
    return None


def build_args(claude, tools, permission_mode=None, resume=None, model=None,
               effort=None, persist=True, extra=None):
    """Argumentos de una consulta no interactiva; el prompt va por stdin."""
    args = [
        claude, "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools", tools,
    ]
    if permission_mode:
        args += ["--permission-mode", permission_mode]
    if resume:
        args += ["--resume", resume]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    if not persist:
        args.append("--no-session-persistence")
    args += list(extra or [])
    return args


def tool_summary(name, tool_input):
    for key in ("file_path", "pattern", "command", "path", "url", "query"):
        value = tool_input.get(key)
        if value:
            value = " ".join(str(value).split())
            if len(value) > 100:
                value = value[:97] + "..."
            return "{} {}".format(name, value)
    return str(name)


def parse_line(line):
    """Convierte una línea de stream-json en eventos (tipo, dato) para la interfaz."""
    line = line.strip()
    if not line:
        return []
    try:
        event = json.loads(line)
    except ValueError:
        return [("raw", line)]
    if not isinstance(event, dict):
        return [("raw", line)]

    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return [("session", event.get("session_id"))]
    if kind == "result":
        return [("result", event)]
    if event.get("parent_tool_use_id"):
        # Lo que escribe un subagente no es la respuesta al usuario.
        return []
    if kind == "stream_event":
        inner = event.get("event") or {}
        if inner.get("type") == "content_block_start":
            if (inner.get("content_block") or {}).get("type") == "text":
                return [("text_start", None)]
        elif inner.get("type") == "content_block_delta":
            delta = inner.get("delta") or {}
            if delta.get("type") == "text_delta":
                return [("text", delta.get("text", ""))]
        return []
    if kind == "assistant":
        # El texto ya llegó por deltas; de aquí sólo interesan las herramientas.
        events = []
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                summary = tool_summary(block.get("name"), block.get("input") or {})
                events.append(("tool", (block.get("id"), summary)))
        return events
    return []


class Runner(object):
    """Corre `claude -p` y entrega los eventos desde un hilo aparte.

    on_event(tipo, dato) y on_exit(código, stderr, detenido) se llaman fuera del
    hilo principal: quien los reciba decide cómo volver a él.
    """

    def __init__(self, args, cwd, prompt, on_event, on_exit):
        self.args = args
        self.cwd = cwd
        self.prompt = prompt
        self.on_event = on_event
        self.on_exit = on_exit
        self.proc = None
        self.stopped = False
        self._stderr = []
        self._tools_seen = set()

    def start(self):
        env = dict(os.environ)
        env["PATH"] = search_path()
        self.proc = subprocess.Popen(
            self.args, cwd=self.cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stderr = threading.Thread(target=self._read_stderr, daemon=True)
        stderr.start()
        threading.Thread(target=self._pump, args=(stderr,), daemon=True).start()

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def _read_stderr(self):
        with self.proc.stderr as stream:
            for raw in stream:
                self._stderr.append(raw.decode("utf-8", "replace"))

    def _pump(self, stderr):
        try:
            self.proc.stdin.write(self.prompt.encode("utf-8"))
            self.proc.stdin.close()
        except OSError:
            pass
        with self.proc.stdout as stream:
            for raw in stream:
                for kind, data in parse_line(raw.decode("utf-8", "replace")):
                    if kind == "tool":
                        tool_id, data = data
                        if tool_id in self._tools_seen:
                            continue
                        self._tools_seen.add(tool_id)
                    self.on_event(kind, data)
        code = self.proc.wait()
        stderr.join(5)
        self.on_exit(code, "".join(self._stderr).strip(), self.stopped)


def strip_fences(text):
    """Quita el bloque ``` si el modelo envolvió el código pese a la instrucción."""
    text = text.strip("\n")
    lines = text.split("\n")
    if len(lines) >= 2 and lines[0].lstrip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1])
    return text


def clip_around(text, start, end, before=60000, after=20000):
    """Parte el texto en (antes, selección, después), acotando lo que rodea al cursor."""
    head = text[max(0, start - before):start]
    tail = text[end:end + after]
    truncated = start > before or len(text) - end > after
    return head, text[start:end], tail, truncated


def pick_cwd(file_path, folders):
    """Carpeta de trabajo: la del proyecto que contiene el archivo, o la del archivo."""
    if file_path:
        inside = [f for f in folders if file_path.startswith(f.rstrip(os.sep) + os.sep)]
        if inside:
            return max(inside, key=len)
        return os.path.dirname(file_path)
    if folders:
        return folders[0]
    return os.path.expanduser("~")


def git_changes(cwd, limit=200000):
    """Devuelve (estado, diff, recortado) de los cambios sin confirmar."""
    def git(*args):
        result = subprocess.run(
            ("git",) + args, cwd=cwd, timeout=30,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
        return result.stdout.decode("utf-8", "replace")

    status = git("status", "--short")
    try:
        diff = git("diff", "HEAD")
    except RuntimeError:
        # Repositorio sin commits todavía.
        diff = git("diff")
    return status, diff[:limit], len(diff) > limit


def terminal_script(claude, cwd, prompt=None, directory=None):
    """Escribe el guion que abre Claude en una terminal y devuelve su ruta.

    Un guion de un solo argumento sirve igual en todas las terminales, que no
    se ponen de acuerdo en cómo reciben un comando con parámetros.
    """
    command = shlex.quote(claude)
    if prompt:
        command += " " + shlex.quote(prompt)
    lines = [
        "#!/bin/sh",
        'rm -f -- "$0"',
        "cd {} || exit 1".format(shlex.quote(cwd)),
        "PATH={}".format(shlex.quote(search_path())),
        "export PATH",
        command,
        "status=$?",
        'if [ "$status" -ne 0 ]; then',
        '    printf \'\\nclaude terminó con código %s. Enter para cerrar.\' "$status"',
        "    read _",
        "fi",
        "",
    ]
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle, path = tempfile.mkstemp(prefix="claude-", suffix=".sh", dir=directory)
    with os.fdopen(handle, "w", encoding="utf-8") as script:
        script.write("\n".join(lines))
    os.chmod(path, 0o700)
    return path


# --- Revisión de ediciones --------------------------------------------------

EDIT_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"


def review_settings(python, hook, backup):
    """Ajustes para `claude --settings` que respaldan cada archivo antes de editarlo."""
    command = "{} {} {}".format(shlex.quote(python), shlex.quote(hook), shlex.quote(backup))
    return json.dumps({"hooks": {"PreToolUse": [{
        "matcher": EDIT_MATCHER,
        "hooks": [{"type": "command", "command": command}],
    }]}})


def _read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def snapshots(backup):
    """Archivos que Claude tocó: (ruta, contenido original o None, contenido actual o None)."""
    manifest = os.path.join(backup, "manifest.jsonl")
    if not os.path.exists(manifest):
        return []
    entries = []
    with open(manifest, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                entry = json.loads(line)
                before = _read(os.path.join(backup, entry["copy"])) if entry["existed"] else None
                entries.append((entry["path"], before, _read(entry["path"])))
    return entries


def edits_diff(backup, cwd, max_lines=400):
    """Devuelve (diff unificado, archivos cambiados, recortado) de la última edición."""
    import difflib
    lines, changed = [], []
    for path, before, after in snapshots(backup):
        if before == after:
            continue
        changed.append(path)
        name = os.path.relpath(path, cwd) if path.startswith(cwd.rstrip(os.sep) + os.sep) else path
        old = (before or b"").decode("utf-8", "replace").splitlines()
        new = (after or b"").decode("utf-8", "replace").splitlines()
        lines.extend(difflib.unified_diff(
            old, new,
            "/dev/null" if before is None else "a/" + name,
            "/dev/null" if after is None else "b/" + name,
            lineterm=""))
    truncated = len(lines) > max_lines
    return "\n".join(lines[:max_lines]), changed, truncated


def mark_applied(backup):
    """Recuerda cómo quedaron los archivos, para notar si alguien los cambió después."""
    import hashlib
    state = {path: (hashlib.sha256(after).hexdigest() if after is not None else None)
             for path, _, after in snapshots(backup)}
    with open(os.path.join(backup, "applied.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle)


def changed_since(backup):
    """Archivos que ya no están como Claude los dejó."""
    import hashlib
    try:
        with open(os.path.join(backup, "applied.json"), encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return []
    changed = []
    for path, _, after in snapshots(backup):
        digest = hashlib.sha256(after).hexdigest() if after is not None else None
        if state.get(path) != digest:
            changed.append(path)
    return changed


def revert_edits(backup):
    """Devuelve cada archivo a como estaba antes de la edición; da los que restauró."""
    restored = []
    for path, before, after in snapshots(backup):
        if before == after:
            continue
        if before is None:
            os.remove(path)
        else:
            with open(path, "wb") as handle:
                handle.write(before)
        restored.append(path)
    return restored


# --- Git, linter y servidor remoto ------------------------------------------

def _run(command, cwd=None, timeout=30):
    result = subprocess.run(
        command, cwd=cwd, timeout=timeout, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return (result.returncode,
            result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"))


def commit_context(cwd, limit=200000):
    """Devuelve (diff, en_preparación, últimos asuntos): lo preparado, o todo si no hay nada."""
    def git(*args):
        code, out, err = _run(("git",) + args, cwd=cwd)
        if code != 0:
            raise RuntimeError(err.strip())
        return out

    diff = git("diff", "--cached")
    staged = bool(diff.strip())
    if not staged:
        try:
            diff = git("diff", "HEAD")
        except RuntimeError:
            diff = git("diff")
    try:
        subjects = git("log", "-12", "--format=%s")
    except RuntimeError:
        subjects = ""
    return diff[:limit], staged, subjects


def find_ruff(cwd, configured=None):
    """Busca ruff: el configurado, el del entorno del proyecto, el del PATH o el de LSP-ruff."""
    if configured:
        return shutil.which(os.path.expanduser(configured), path=search_path())
    for folder in (".venv", "venv", "env"):
        candidate = os.path.join(cwd, folder, "bin", "ruff")
        if os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("ruff", path=search_path())
    if found:
        return found
    # LSP-ruff lo instala en un entorno oculto (.venv), que glob no recorre.
    storage = os.path.expanduser("~/.cache/sublime-text/Package Storage/LSP-ruff")
    for folder, _, files in os.walk(storage):
        candidate = os.path.join(folder, "ruff")
        if "ruff" in files and os.path.basename(folder) == "bin" and os.access(candidate, os.X_OK):
            return candidate
    return None


def ruff_diagnostics(ruff, path, cwd):
    """Avisos de ruff para un archivo, uno por línea como `ruta:línea:col: código mensaje`."""
    code, out, err = _run(
        [ruff, "check", "--output-format=concise", "--no-fix", "--quiet", path], cwd=cwd)
    if code not in (0, 1):
        raise RuntimeError(err.strip() or out.strip())
    prefix = cwd.rstrip(os.sep) + os.sep
    lines = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            line = line[len(prefix):]
        if line and ":" in line and not line.startswith(("Found ", "All checks", "[*]", "No fixes")):
            lines.append(line)
    return lines


SAFE_NAME = __import__("re").compile(r"^[A-Za-z0-9_.@:/\-]+$")


def ssh_run(host, remote_command, ssh_args=None, timeout=40):
    """Ejecuta un comando en el servidor sin pedir nada por teclado."""
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    command += list(ssh_args or []) + [host, remote_command]
    code, out, err = _run(command, timeout=timeout)
    if code != 0:
        raise RuntimeError(err.strip() or out.strip() or "ssh terminó con código {}".format(code))
    return out


def parse_services(listing):
    """Convierte la lista remota (nombre<TAB>estado por línea) en pares."""
    services = []
    for line in listing.splitlines():
        name, _, status = line.strip().partition("\t")
        if name and SAFE_NAME.match(name):
            services.append((name, status.strip()))
    return services


def logs_command(template, name, lines):
    """Arma el comando remoto de logs; el nombre se valida porque va a un shell."""
    if not SAFE_NAME.match(name):
        raise ValueError("Nombre de servicio no válido: {!r}".format(name))
    return template.format(name=shlex.quote(name), lines=int(lines))


# --- Pruebas, adjuntos, historial y estado del servidor ----------------------

SKIPPED_DIRS = {"node_modules", "__pycache__", "venv", "env", "build", "dist", "site-packages"}


def find_project_python(cwd):
    """El Python del entorno del proyecto, que es el que tiene sus dependencias."""
    for folder in (".venv", "venv", "env"):
        candidate = os.path.join(cwd, folder, "bin", "python")
        if os.access(candidate, os.X_OK):
            return candidate
    return find_python()


def is_test_file(path):
    name = os.path.basename(path)
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _walk(cwd):
    for folder, dirs, files in os.walk(cwd):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIPPED_DIRS)
        yield folder, sorted(files)


def find_tests_for(path, cwd):
    """Archivo de pruebas de un módulo: test_<nombre>.py o <nombre>_test.py."""
    stem = os.path.splitext(os.path.basename(path))[0]
    wanted = {"test_{}.py".format(stem), "{}_test.py".format(stem)}
    found = [os.path.join(folder, name)
             for folder, files in _walk(cwd) for name in files if name in wanted]
    return min(found, key=lambda item: (item.count(os.sep), item)) if found else None


def run_tests(python, target, cwd, timeout=300):
    """Corre pytest sobre un archivo; devuelve (código, salida)."""
    code, out, err = _run(
        [python, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", target],
        cwd=cwd, timeout=timeout)
    return code, (out + ("\n" + err if err.strip() else "")).strip()


def last_line(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1].strip("= ") if lines else ""


def project_files(cwd, limit=8000):
    """Archivos del proyecto para adjuntar: los que git no ignora, o un recorrido."""
    try:
        code, out, _ = _run(["git", "ls-files", "-co", "--exclude-standard"], cwd=cwd)
        if code == 0 and out.strip():
            return sorted(out.splitlines())[:limit]
    except (OSError, subprocess.TimeoutExpired):
        pass
    files = []
    for folder, names in _walk(cwd):
        for name in names:
            files.append(os.path.relpath(os.path.join(folder, name), cwd))
            if len(files) >= limit:
                return files
    return files


MENTION = __import__("re").compile(r"(?<![\w@])@([^\s@]+)")


def mentioned_files(message, cwd):
    """Rutas escritas como @ruta en el mensaje que existen dentro del proyecto."""
    found = []
    for raw in MENTION.findall(message):
        path = raw.rstrip(".,;:!?)")
        if path not in found and os.path.isfile(os.path.join(cwd, path)):
            found.append(path)
    return found


def history_title(text, limit=80):
    """Título de una conversación: su encabezado o el primer mensaje."""
    for line in text.splitlines():
        line = line.strip()
        for prefix in ("## ", "❯ "):
            if line.startswith(prefix) and line[len(prefix):].strip():
                title = line[len(prefix):].strip()
                return title if len(title) <= limit else title[:limit - 1] + "…"
    return "Conversación"


def history_save(folder, entry, keep=50):
    """Guarda una conversación (una por sesión) y conserva sólo las más recientes."""
    if not SAFE_NAME.match(entry["session"]):
        return
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, entry["session"].replace("/", "_") + ".json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(entry, handle, ensure_ascii=False)
    for old in history_list(folder)[keep:]:
        try:
            os.remove(old["file"])
        except OSError:
            pass


def history_list(folder):
    """Conversaciones guardadas, de la más reciente a la más antigua."""
    entries = []
    try:
        names = os.listdir(folder)
    except OSError:
        return entries
    for name in names:
        if name.endswith(".json"):
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    entry = json.load(handle)
                entry["file"] = path
                entry["session"], entry["text"], entry["updated"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            entries.append(entry)
    return sorted(entries, key=lambda item: item["updated"], reverse=True)


def age_text(seconds):
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return "hace {} {}".format(int(seconds // size), unit)
    return "recién"


def service_report(services):
    """Separa los servicios con problemas de los que están en marcha."""
    problems, healthy = [], []
    for name, status in services:
        lowered = status.lower()
        fine = lowered.startswith(("up", "running", "active")) and "unhealthy" not in lowered \
            and "restarting" not in lowered
        (healthy if fine else problems).append((name, status))
    return problems, healthy

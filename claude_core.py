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
import time

IS_WINDOWS = os.name == "nt"

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
    for name in ("python3", "python"):
        found = shutil.which(name, path=search_path())
        if found:
            return found
    return None


def spawn_options():
    """Grupo de procesos propio: así se puede cortar al CLI junto con sus hijos."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def kill_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def find_terminal(configured=None):
    if configured:
        return shutil.which(os.path.expanduser(configured), path=search_path())
    for name in TERMINALS:
        found = shutil.which(name, path=search_path())
        if found:
            return found
    return None


def build_args(claude, tools, permission_mode=None, resume=None, model=None,
               effort=None, persist=True, extra=None, system=None, live=False):
    """Argumentos de una consulta no interactiva; el prompt va por stdin.

    live: el proceso queda vivo, recibe cada mensaje como una línea JSON y pregunta
    por stdout antes de usar una herramienta que necesite permiso.
    """
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
    if system:
        args += ["--append-system-prompt", system]
    if live:
        args += ["--input-format", "stream-json", "--permission-prompt-tool", "stdio"]
    args += list(extra or [])
    return args


def join_tools(*groups):
    """Une listas de herramientas separadas por comas, sin repetir y en orden."""
    names = []
    for group in groups:
        for name in (group or "").split(","):
            if name.strip() and name.strip() not in names:
                names.append(name.strip())
    return ",".join(names)


def chat_note(tools, can_edit, plan=False, asks=False):
    """Le dice al modelo qué puede hacer en este turno.

    Sin esto no sabe por qué no logra editar y no puede indicar cómo se habilita.
    asks: el usuario aprueba desde el editor lo que necesite permiso.
    """
    names = [name.strip() for name in (tools or "").split(",") if name.strip()]
    parts = ["Respondes en el chat de un plugin de Sublime Text. Herramientas de este "
             "turno: {}.".format(", ".join(names) or "ninguna")]
    if plan:
        parts.append(
            "El chat está en modo plan: investiga leyendo lo que haga falta y propón un "
            "plan concreto (qué archivos, qué cambia en cada uno, en qué orden y cómo se "
            "verifica), sin modificar nada. Cuando el usuario lo apruebe pasará el chat a "
            "edición con «Claude: Elegir modo del chat…» (Ctrl+Alt+C, U) y te pedirá "
            "aplicarlo.")
    elif can_edit:
        parts.append(
            "El chat tiene la edición activada: cuando te pidan un cambio, aplícalo en los "
            "archivos en vez de sólo mostrar el código. Sólo puedes editar dentro de la "
            "carpeta de trabajo.")
    else:
        parts.append(
            "El chat está en sólo lectura: no puedes crear ni modificar archivos. Si te "
            "piden un cambio, muéstralo y avisa que, para que lo apliques tú, hay que "
            "activar «Claude: Permitir o impedir que el chat edite archivos» "
            "(Ctrl+Alt+C, M) y volver a pedirlo.")
    if "Bash" not in names:
        parts.append("No puedes ejecutar comandos.")
    elif asks:
        parts.append(
            "Puedes ejecutar comandos con Bash; el usuario aprueba cada uno desde el "
            "editor, así que pide sólo los necesarios y de a uno. Si rechaza uno, no "
            "insistas: explica qué habría hecho.")
    return " ".join(parts)


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
    if kind == "control_request":
        request = event.get("request") or {}
        if request.get("subtype") == "can_use_tool":
            tool_input = request.get("input") or {}
            return [("permission", {
                "id": event.get("request_id"), "tool": request.get("tool_name"),
                "input": tool_input,
                "summary": tool_summary(request.get("tool_name"), tool_input),
                "description": request.get("description") or "",
                "blocked_path": request.get("blocked_path") or ""})]
        return []
    if kind == "control_response":
        return []
    if kind == "rate_limit_event":
        info = event.get("rate_limit_info") or {}
        # «allowed» llega en cada consulta y no dice nada que haya que mostrar.
        if info.get("status") in ("allowed_warning", "rejected"):
            return [("limit", info)]
        return []
    if event.get("parent_tool_use_id"):
        # Lo que escribe un subagente no es la respuesta al usuario; sólo se avisa
        # qué herramienta usa, para que la espera no parezca un cuelgue.
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    return [("activity", "subagente · {}…".format(
                        tool_summary(block.get("name"), block.get("input") or {})))]
        return []
    if kind == "stream_event":
        inner = event.get("event") or {}
        if inner.get("type") == "content_block_start":
            block = inner.get("content_block") or {}
            if block.get("type") == "text":
                return [("text_start", None)]
            # Mientras piensa o arma una herramienta no llega texto: se avisa qué hace.
            if block.get("type") in ("thinking", "redacted_thinking"):
                return [("activity", "pensando…")]
            if block.get("type") == "tool_use":
                return [("activity", "preparando {}…".format(block.get("name") or "herramienta"))]
        elif inner.get("type") == "content_block_delta":
            delta = inner.get("delta") or {}
            if delta.get("type") == "text_delta":
                return [("text", delta.get("text", ""))]
        return []
    if kind == "assistant":
        # El texto ya llegó por deltas; de aquí sólo interesan las herramientas y
        # cuánto contexto ocupó esta llamada al modelo.
        events = []
        usage = (event.get("message") or {}).get("usage")
        if isinstance(usage, dict):
            events.append(("usage", sum(int(usage.get(key) or 0) for key in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                "output_tokens"))))
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                summary = tool_summary(block.get("name"), block.get("input") or {})
                events.append(("tool", (block.get("id"), summary)))
        return events
    if kind == "user":
        content = (event.get("message") or {}).get("content")
        if isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content):
            # La herramienta terminó; el modelo todavía no empieza a contestar.
            return [("activity", "esperando la respuesta…")]
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
            **spawn_options())
        stderr = threading.Thread(target=self._read_stderr, daemon=True)
        stderr.start()
        threading.Thread(target=self._pump, args=(stderr,), daemon=True).start()

    def stop(self):
        self.stopped = True
        kill_tree(self.proc)

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


class Turn(object):
    """Un mensaje en curso dentro de una Session; se detiene igual que un Runner."""

    def __init__(self, session, on_event, on_exit):
        self.session = session
        self.on_event = on_event
        self.on_exit = on_exit
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.session.interrupt(self)


class Session(object):
    """Un `claude -p` que queda vivo y recibe cada mensaje por stdin.

    Ahorra el arranque de cada turno y deja que el CLI pregunte antes de usar una
    herramienta (evento «permission», que se contesta con answer()). Los eventos
    se entregan igual que en Runner; on_exit llega al terminar cada turno, con
    código 0, o con el código de salida si el proceso murió.
    """

    STOP_GRACE = 5  # segundos que se espera a que la interrupción termine el turno

    def __init__(self, args, cwd):
        self.args = args
        self.cwd = cwd
        self.proc = None
        self.turn = None
        self.events = 0      # eventos entendidos: en 0, el proceso nunca llegó a arrancar
        self.allowed = set()  # herramientas permitidas para el resto de la conversación
        self._pending = set()
        self._stderr = []
        self._tools_seen = set()
        self._lock = threading.Lock()

    def start(self):
        env = dict(os.environ)
        env["PATH"] = search_path()
        self.proc = subprocess.Popen(
            self.args, cwd=self.cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **spawn_options())
        stderr = threading.Thread(target=self._read_stderr, daemon=True)
        stderr.start()
        threading.Thread(target=self._pump, args=(stderr,), daemon=True).start()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def busy(self):
        return self.turn is not None

    def ask(self, prompt, on_event, on_exit):
        turn = self.turn = Turn(self, on_event, on_exit)
        if not self._send({"type": "user", "message": {"role": "user", "content": prompt}}):
            self.turn = None
            on_exit(1, "El proceso de Claude ya no está en marcha.", False)
        return turn

    def answer(self, request_id, allow, tool_input=None, message=""):
        """Contesta una solicitud de permiso; si el turno ya terminó, no hace nada."""
        if request_id not in self._pending:
            return
        self._pending.discard(request_id)
        if allow:
            decision = {"behavior": "allow", "updatedInput": tool_input or {}}
        else:
            decision = {"behavior": "deny",
                        "message": message or "El usuario rechazó esta acción desde el editor."}
        self._send({"type": "control_response", "response": {
            "subtype": "success", "request_id": request_id, "response": decision}})

    def interrupt(self, turn):
        if self.turn is not turn:
            return
        self._send({"type": "control_request", "request_id": "stop-{}".format(id(turn)),
                    "request": {"subtype": "interrupt"}})

        def insist():
            # El CLI no cerró el turno: se corta el proceso, que sí lo termina.
            if self.turn is turn:
                self.close()

        timer = threading.Timer(self.STOP_GRACE, insist)
        timer.daemon = True
        timer.start()

    def close(self):
        kill_tree(self.proc)

    def _send(self, payload):
        try:
            with self._lock:
                self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def _read_stderr(self):
        with self.proc.stderr as stream:
            for raw in stream:
                self._stderr.append(raw.decode("utf-8", "replace"))

    def _pump(self, stderr):
        with self.proc.stdout as stream:
            for raw in stream:
                for kind, data in parse_line(raw.decode("utf-8", "replace")):
                    self.events += 1
                    self._deliver(kind, data)
        code = self.proc.wait()
        stderr.join(5)
        try:
            with self._lock:
                self.proc.stdin.close()
        except OSError:
            pass
        turn, self.turn = self.turn, None
        self._pending.clear()
        if turn is not None:
            turn.on_exit(code, "".join(self._stderr).strip(), turn.stopped)

    def _deliver(self, kind, data):
        turn = self.turn
        if kind == "tool":
            tool_id, data = data
            if tool_id in self._tools_seen:
                return
            self._tools_seen.add(tool_id)
        elif kind == "permission":
            self._pending.add(data["id"])
            if data["tool"] in self.allowed or turn is None:
                self.answer(data["id"], turn is not None, data["input"])
                return
        if turn is None:
            return
        turn.on_event(kind, data)
        if kind == "result":
            self.turn = None
            self._pending.clear()
            turn.on_exit(0, "", turn.stopped)


def context_text(tokens, result):
    """Cuánto de la ventana de contexto ocupó la última llamada: «38 %» o «76k»."""
    if not tokens:
        return ""
    windows = [int(usage.get("contextWindow") or 0)
               for usage in ((result or {}).get("modelUsage") or {}).values()
               if isinstance(usage, dict)]
    window = max(windows) if windows else 0
    if window:
        return "contexto {:.0f} %".format(min(100.0, 100.0 * tokens / window))
    return "contexto {:.0f}k".format(tokens / 1000.0)


LIMIT_NAMES = {
    "five_hour": "de 5 horas", "seven_day": "semanal", "seven_day_opus": "semanal de Opus",
    "seven_day_sonnet": "semanal de Sonnet", "overage": "de uso adicional",
    "seven_day_overage_included": "semanal",
}


def limit_text(info, now=None):
    """Aviso de un evento de límite de uso del CLI, o "" si no hay nada que avisar."""
    status = info.get("status")
    if status not in ("allowed_warning", "rejected"):
        return ""
    name = LIMIT_NAMES.get(info.get("rateLimitType"), "")
    text = "límite de uso {}".format(name).strip()
    if status == "rejected":
        text += " alcanzado"
    elif isinstance(info.get("utilization"), (int, float)):
        used = info["utilization"]
        text += " al {:.0f} %".format(used * 100 if used <= 1 else used)
    else:
        text += " cerca de agotarse"
    resets = info.get("resetsAt")
    if resets:
        now = time.time() if now is None else now
        moment = time.localtime(resets)
        same_day = time.localtime(now)[:3] == moment[:3]
        text += "; se restablece {}".format(
            time.strftime("a las %H:%M" if same_day else "el %d-%m a las %H:%M", moment))
    return text


def buffer_diff(disk, text, name, context=3):
    """Diff del búfer contra el archivo en disco, o None si no conviene enviarlo.

    Un archivo grande con dos líneas cambiadas no tiene por qué viajar entero: el
    modelo puede leer el de disco y aplicar el diff.
    """
    import difflib
    if disk is None or disk == text:
        return None
    diff = "\n".join(difflib.unified_diff(
        disk.splitlines(), text.splitlines(), "a/" + name, "b/" + name,
        n=context, lineterm=""))
    return diff if diff and len(diff) < len(text) else None


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


def edits_report(backup, cwd, max_lines=400):
    """El diff de la última edición, con todos sus archivos aunque sea largo.

    El tope de líneas se reparte entre los archivos: de cada uno queda el comienzo,
    así ninguno desaparece del resumen. Los totales cuentan el diff completo.
    """
    import difflib
    files, changed, added, removed = [], [], 0, 0
    for path, before, after in snapshots(backup):
        if before == after:
            continue
        changed.append(path)
        name = os.path.relpath(path, cwd) if path.startswith(cwd.rstrip(os.sep) + os.sep) else path
        old = (before or b"").decode("utf-8", "replace").splitlines()
        new = (after or b"").decode("utf-8", "replace").splitlines()
        lines = list(difflib.unified_diff(
            old, new,
            "/dev/null" if before is None else "a/" + name,
            "/dev/null" if after is None else "b/" + name,
            lineterm=""))
        added += sum(1 for line in lines[2:] if line.startswith("+"))
        removed += sum(1 for line in lines[2:] if line.startswith("-"))
        files.append(lines)
    quota = max(max_lines // max(len(files), 1), min(max_lines, 12))
    shown, truncated = [], False
    for lines in files:
        shown.extend(lines[:quota])
        if len(lines) > quota:
            truncated = True
            if quota > 3:
                shown.append("… {} líneas más".format(len(lines) - quota))
    return {"diff": "\n".join(shown), "changed": changed, "truncated": truncated,
            "added": added, "removed": removed}


def edits_diff(backup, cwd, max_lines=400):
    """Devuelve (diff unificado, archivos cambiados, recortado) de la última edición."""
    report = edits_report(backup, cwd, max_lines)
    return report["diff"], report["changed"], report["truncated"]


def diff_folds(text):
    """Tramos a plegar en los bloques ```diff de un texto: uno por archivo.

    Devuelve (inicio, fin, agregadas, quitadas) por archivo; el tramo va desde el
    final de la línea `+++` hasta el final de la última línea de ese archivo, así
    que plegado queda a la vista sólo el encabezado.
    """
    lines, offset = [], 0
    for line in text.split("\n"):
        lines.append((offset, line))
        offset += len(line) + 1
    folds, inside, current = [], False, None

    def close(end):
        if current and end > current[0]:
            folds.append((current[0], end, current[1], current[2]))

    for index, (start, line) in enumerate(lines):
        if not inside:
            inside = line == "```diff"
            continue
        following = [text for _, text in lines[index + 1:index + 3]]
        header = (line.startswith("--- ") and len(following) == 2
                  and following[0].startswith("+++ ") and following[1].startswith("@@"))
        # Sin strip: una línea de contexto « ```» es parte del diff, no su cierre.
        if line == "```" or header:
            # Termina en el salto de línea anterior, que queda fuera del pliegue.
            close(start - 1)
            current = None
            inside = header
        elif current is None:
            if line.startswith("+++ "):
                current = [start + len(line), 0, 0]
        elif line.startswith("+"):
            current[1] += 1
        elif line.startswith("-"):
            current[2] += 1
    return folds


def code_folds(text, min_lines, keep=4):
    """Tramos a plegar en los bloques de código largos de una respuesta.

    De cada bloque ``` con más de min_lines líneas quedan a la vista las primeras
    `keep`; los ```diff no cuentan, que se pliegan por archivo.
    """
    folds, offset, opened = [], 0, None
    if not min_lines or min_lines <= 0:
        return folds
    for line in text.split("\n"):
        stripped = line.strip()
        if opened is None:
            if stripped.startswith("```"):
                opened = {"diff": stripped[3:].strip() == "diff", "lines": []}
        elif stripped == "```":
            body = opened["lines"]
            if not opened["diff"] and len(body) > min_lines:
                start, last = body[keep - 1], body[-1]
                folds.append((start[0] + len(start[1]), last[0] + len(last[1])))
            opened = None
        else:
            opened["lines"].append((offset, line))
        offset += len(line) + 1
    return folds


def rotate_backup(backup, final):
    """Aparta el respaldo de un turno y deja la carpeta lista para el siguiente.

    El hook de un proceso que sigue vivo escribe siempre en la misma carpeta.
    """
    if os.path.isdir(backup):
        os.rename(backup, final)
    os.makedirs(backup, exist_ok=True)
    return final


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
    for path, before, after in snapshots(backup):
        if before == after:
            # Ya está como antes de la edición (se revirtió): no hay nada que perder.
            continue
        digest = hashlib.sha256(after).hexdigest() if after is not None else None
        if state.get(path) != digest:
            changed.append(path)
    return changed


def edited_files(backup):
    """Archivos de la edición que siguen distintos de como estaban antes."""
    return [path for path, before, after in snapshots(backup) if before != after]


def revert_edits(backup, only=None):
    """Devuelve cada archivo a como estaba antes de la edición; da los que restauró.

    only: rutas a las que se limita; el resto de la edición queda en pie.
    """
    restored = []
    for path, before, after in snapshots(backup):
        if before == after or (only is not None and path not in only):
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

"""Claude Code dentro de Sublime Text: un chat en una pestaña, edición y terminal."""
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import sublime
import sublime_plugin

from . import claude_core as core

SETTINGS = "ClaudeCode.sublime-settings"
SYNTAX = "Packages/ClaudeCode/ClaudeChat.sublime-syntax"
STATUS_KEY = "claude_code"
BUFFER_LIMIT = 150000

# Lo que el usuario escribe va después de la última marca; todo lo anterior es
# conversación, y una edición que la toque se deshace sola.
MARK = "❯ "

# Estado de la conversación, guardado en la propia pestaña para que sobreviva a
# un reinicio de Sublime.
CHAT = "claude_chat"
K_START = "claude_input_start"
K_SESSION = "claude_session"
K_CWD = "claude_cwd"
K_EDIT = "claude_can_edit"
K_CONTEXT = "claude_context"
K_BACKUP = "claude_last_edit"
K_NOTICE = "claude_notice"

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks", "snapshot.py")
REMOTE_LIST = "docker ps -a --format '{{.Names}}\\t{{.Status}}'"
REMOTE_LOGS = "docker logs --tail {lines} {name} 2>&1"

REFERENCE = re.compile(r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+):(\d+)")
HISTORY_COMMANDS = ("undo", "redo", "redo_or_repeat", "soft_undo", "soft_redo")

REVIEW_FILE = (
    "Revisa {target} buscando errores: bugs, casos borde, excepciones sin manejar, "
    "problemas de seguridad y de tipos. No modifiques nada. Por cada hallazgo indica "
    "`ruta:línea`, qué falla y cómo se arregla, del más grave al menos grave. "
    "Si no encuentras nada, dilo en una línea."
)
REVIEW_CHANGES = (
    "Revisa los cambios sin confirmar de este repositorio buscando errores: bugs, "
    "regresiones, casos borde y problemas de seguridad. Lee los archivos que necesites "
    "para entender el contexto, pero no modifiques nada. Por cada hallazgo indica "
    "`ruta:línea`, qué falla y cómo se arregla, del más grave al menos grave. "
    "Si no encuentras nada, dilo en una línea."
)
EXPLAIN_BUILD = (
    "Esta es la salida del último build o ejecución lanzado desde el editor. Explica la "
    "causa de cada error y propone la corrección, citando `ruta:línea`. Lee los archivos "
    "que necesites, pero no modifiques nada."
)
EDIT_FILE = (
    "En el archivo `{rel}`: {instruction}\n\n"
    "Aplica el cambio directamente en el archivo, tocando sólo lo necesario, y al final "
    "resume en pocas líneas qué cambiaste."
)
FIX_DIAGNOSTICS = (
    "Corrige en `{rel}` estos avisos del linter:\n\n{diagnostics}\n\n"
    "Aplica las correcciones directamente en el archivo, tocando sólo lo que cada aviso "
    "señala. Si alguno es un falso positivo, o arreglarlo cambiaría el comportamiento, "
    "déjalo como está y dilo. Al final resume qué corregiste y qué dejaste."
)
WRITE_TESTS = (
    "Escribe pruebas con pytest para {target} de `{rel}`.\n\n"
    "Antes mira cómo están organizadas las pruebas del proyecto (carpeta, nombres, "
    "fixtures) y sigue esa convención; si ya hay un archivo de pruebas para este módulo, "
    "agrégalas ahí. Cubre el caso normal, los casos borde y los errores esperados. No "
    "modifiques el código que se prueba: si encuentras un bug, déjalo anotado. No puedes "
    "ejecutar las pruebas, así que al final da el comando para correrlas."
)
COMMIT_MESSAGE = (
    "Escribe el mensaje de commit para estos cambios. Sigue el idioma y el estilo de los "
    "últimos commits del repositorio. Primera línea de 72 caracteres como máximo; si hace "
    "falta, un cuerpo breve que explique el porqué. Responde únicamente con el mensaje, "
    "sin comillas ni bloque ```.\n\n<ultimos_commits>\n{subjects}</ultimos_commits>\n\n"
    "<diff>\n{diff}</diff>"
)
EXPLAIN_ERROR = (
    "Este error salió al ejecutar código de este proyecto. Explica la causa y propone la "
    "corrección, citando `ruta:línea`. Lee los archivos que necesites, pero no modifiques "
    "nada."
)
REMOTE_DIAGNOSE = (
    "Estos son los últimos {lines} registros de `{name}` en el servidor `{host}`. "
    "Diagnostica: qué errores hay, su causa probable y qué revisar o corregir. Si el "
    "código de este proyecto corresponde a ese servicio, lee los archivos que necesites y "
    "cita `ruta:línea`. No modifiques nada."
)
FIX_TESTS = (
    "Corrí `{command}` y hay pruebas que fallan:\n\n<salida>\n{output}\n</salida>\n\n"
    "Encuentra la causa leyendo el código y las pruebas. Si el bug está en el código, "
    "corrígelo. Si la que está mal es la prueba, no la debilites (nada de skip, xfail ni "
    "ajustar el valor esperado al resultado): explica por qué y no la toques. Al final "
    "resume qué cambiaste; no puedes volver a correr las pruebas, eso lo hago yo."
)
MODELS = (
    ("", "Predeterminado", "El que tenga configurado Claude Code"),
    ("opus", "Opus", "El más capaz: revisiones y cambios delicados"),
    ("sonnet", "Sonnet", "Equilibrado: el trabajo de todos los días"),
    ("haiku", "Haiku", "El más rápido: preguntas cortas"),
)
WRITE_CODE = (
    "Escribe código para {action} en `{name}`{syntax}.\n\n"
    "Instrucción: {instruction}\n\n"
    "{body}\n\n"
    "Responde únicamente con el código que va en ese lugar, con la indentación que "
    "corresponde: sin explicaciones y sin bloque ```."
)

_runners = {}    # id de ventana -> consulta en curso
_last_file = {}  # id de ventana -> id de la última pestaña que no es el chat
_transcripts = {}  # id del chat -> conversación hasta la marca, para protegerla
_restoring = set()
_testing = set()   # ids de ventana con pytest corriendo


def _history_dir():
    return os.path.join(sublime.packages_path(), "User", "ClaudeCode", "history")


def _save_history(view):
    """Guarda la conversación para poder retomarla desde «Conversaciones anteriores»."""
    state = view.settings()
    session = state.get(K_SESSION)
    text = _transcripts.get(view.id(), "")
    if not session or not text.endswith(MARK):
        return
    text = text[:-len(MARK)].rstrip("\n")
    try:
        core.history_save(_history_dir(), {
            "session": session, "cwd": state.get(K_CWD), "edit": bool(state.get(K_EDIT)),
            "title": core.history_title(text), "updated": time.time(), "text": text})
    except OSError:
        pass


def _settings():
    return sublime.load_settings(SETTINGS)


def _is_chat(view):
    return view is not None and bool(view.settings().get(CHAT))


def _file_view(window):
    """La pestaña sobre la que se trabaja, aunque el foco esté en el chat."""
    view = window.active_view()
    if view is not None and not _is_chat(view):
        return view
    wanted = _last_file.get(window.id())
    for candidate in window.views():
        if candidate.id() == wanted and not _is_chat(candidate):
            return candidate
    for group in range(window.num_groups()):
        candidate = window.active_view_in_group(group)
        if candidate is not None and not _is_chat(candidate):
            return candidate
    return None


# --- La pestaña de chat -----------------------------------------------------

def _find_chat(window):
    for view in window.views():
        if _is_chat(view):
            return view
    return None


def _create_chat(window):
    working = _last_file.get(window.id())
    view = window.new_file()
    view.set_name("Claude")
    view.set_scratch(True)
    view.assign_syntax(SYNTAX)
    settings = view.settings()
    settings.set(CHAT, True)
    # Al nacer, la pestaña todavía no estaba marcada como chat y quedó anotada
    # como archivo de trabajo: se repone el que había.
    if working is None:
        _last_file.pop(window.id(), None)
    else:
        _last_file[window.id()] = working
    for key, value in (
        ("word_wrap", True), ("wrap_width", 0), ("gutter", False),
        ("line_numbers", False), ("highlight_line", False), ("rulers", []),
        ("draw_indent_guides", False), ("spell_check", False),
        ("auto_indent", False), ("smart_indent", False),
        ("auto_match_enabled", False), ("auto_complete", False),
        ("tab_completion", False), ("scroll_past_end", False),
    ):
        settings.set(key, value)
    if _settings().get("chat_position", "right") == "right":
        if window.num_groups() == 1:
            window.set_layout({
                "cols": [0.0, 0.6, 1.0], "rows": [0.0, 1.0],
                "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
            })
        group = window.num_groups() - 1
        if window.get_view_index(view)[0] != group:
            window.set_view_index(view, group, len(window.views_in_group(group)))
    _end_turn(view)
    return view


def _open_chat(window, focus=True):
    view = _find_chat(window) or _create_chat(window)
    if focus:
        window.focus_view(view)
    return view


def _write(view, text):
    if text and view.is_valid():
        view.run_command(
            "append", {"characters": text, "force": True, "scroll_to_end": True})


def _tail(view):
    return view.substr(sublime.Region(max(0, view.size() - 2), view.size()))


def _block(view):
    """Deja una línea en blanco antes del próximo bloque."""
    tail = _tail(view)
    if view.size() and not tail.endswith("\n\n"):
        _write(view, "\n" if tail.endswith("\n") else "\n\n")


def _line(view):
    if view.size() and not _tail(view).endswith("\n"):
        _write(view, "\n")


def _begin_turn(view):
    """Retira la marca de entrada y devuelve lo que el usuario llevaba escrito."""
    if not view.is_valid():
        return ""
    settings = view.settings()
    start = settings.get(K_START)
    draft = ""
    if start is not None:
        settings.erase(K_START)
        start = min(start, view.size())
        draft = view.substr(sublime.Region(start, view.size()))
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {
            "a": max(0, start - len(MARK)), "b": view.size(), "text": ""})
    view.set_read_only(True)
    return draft


def _end_turn(view, draft=""):
    """Deja la marca lista para que el usuario escriba su próximo mensaje."""
    if not view.is_valid():
        return
    _block(view)
    _write(view, MARK + draft)
    start = view.size() - len(draft)
    _transcripts[view.id()] = view.substr(sublime.Region(0, start))
    view.settings().set(K_START, start)
    view.set_read_only(False)
    view.sel().clear()
    view.sel().add(sublime.Region(view.size(), view.size()))
    view.show(view.size())
    if hasattr(view, "clear_undo_stack"):
        # Deshacer no debe llevarse la conversación, sólo lo que se escribe.
        sublime.set_timeout(lambda: view.is_valid() and view.clear_undo_stack(), 0)


def _note(window, text):
    """Escribe un aviso suelto en el chat, fuera de una consulta."""
    view = _open_chat(window)
    draft = _begin_turn(view)
    _block(view)
    _write(view, text)
    _end_turn(view, draft)


def _set_edit(view, enabled):
    view.settings().set(K_EDIT, bool(enabled))
    view.set_name("Claude ✎" if enabled else "Claude")


# --- Contexto del editor ----------------------------------------------------

class _Context(object):
    """Lo que el editor sabe en el momento de la consulta."""

    def __init__(self, window):
        self.view = _file_view(window)
        view = self.view
        self.path = view.file_name() if view else None
        self.cwd = core.pick_cwd(self.path, window.folders())
        self.rel = os.path.relpath(self.path, self.cwd) if self.path else None
        self.dirty = bool(view and view.is_dirty())
        syntax = view.syntax() if view else None
        self.syntax = syntax.name if syntax else ""
        self.region = None
        if view:
            for region in view.sel():
                if not region.empty():
                    self.region = region
                    break

    def signature(self):
        """Cambia cuando cambia lo que describe(): evita repetirlo en cada mensaje."""
        view = self.view
        if view is None:
            return []
        region = [self.region.begin(), self.region.end()] if self.region else []
        changes = view.change_count() if self.dirty or not self.path else 0
        return [self.path or view.id(), region, changes]

    def describe(self):
        """Texto que acompaña al prompt: archivo, búfer sin guardar y selección."""
        view = self.view
        if view is None:
            return ""
        parts = []
        if self.path and not self.dirty:
            parts.append("Archivo abierto en el editor: `{}`.".format(self.rel))
        elif view.size():
            if self.path:
                title = "Archivo abierto en el editor: `{}`. Tiene cambios sin guardar".format(
                    self.rel)
            else:
                title = "Búfer sin guardar{}".format(
                    " ({})".format(self.syntax) if self.syntax else "")
            text = view.substr(sublime.Region(0, min(view.size(), BUFFER_LIMIT)))
            note = " (recortado)" if view.size() > BUFFER_LIMIT else ""
            parts.append("{}; este es su contenido actual{}:\n<contenido>\n{}\n</contenido>".format(
                title, note, text))
        if self.region is not None:
            first = view.rowcol(self.region.begin())[0] + 1
            last = view.rowcol(self.region.end())[0] + 1
            parts.append("Selección (líneas {}-{}):\n<seleccion>\n{}\n</seleccion>".format(
                first, last, view.substr(self.region)))
        return "\n\n".join(parts)


def _with_context(prompt, context):
    described = context.describe()
    return "{}\n\n{}".format(prompt, described) if described else prompt


# --- Consultas --------------------------------------------------------------

def _ask(window, title, prompt, cwd, tools, mode=None, resume=None, echo=None,
         context=None, quiet=False, on_result=None, chat_turn=False):
    """Lanza una consulta y escribe la respuesta en el chat.

    chat_turn: el mensaje ya está escrito en el chat (lo envió el usuario desde ahí).
    quiet: no toca el chat salvo que falle (se usa para generar código).
    """
    chat = _find_chat(window) if chat_turn else None
    if window.id() in _runners:
        window.status_message("Claude sigue trabajando; usa «Claude: Detener» para cortarlo.")
        return
    settings = _settings()
    claude = core.find_claude(settings.get("claude_path"))
    if claude is None:
        sublime.error_message(
            "No se encontró el ejecutable de Claude Code.\n\n"
            "Indica su ruta en «claude_path» (Preferences: Claude Settings).")
        if chat is not None:
            _end_turn(chat)
        return

    extra = list(settings.get("extra_args") or [])
    backup = None
    python = core.find_python()
    if mode == "acceptEdits" and settings.get("review_edits", True) and python:
        # Un hook copia cada archivo antes de que Claude lo toque: de ahí salen el
        # diff que se muestra al terminar y la posibilidad de revertir.
        backup = os.path.join(sublime.cache_path(), "ClaudeCode", "edits",
                              str(int(time.time() * 1000)))
        os.makedirs(backup, exist_ok=True)
        extra += ["--settings", core.review_settings(python, HOOK, backup)]
    args = core.build_args(
        claude, tools, permission_mode=mode, resume=resume,
        model=settings.get("model"), effort=settings.get("effort"),
        persist=not quiet, extra=extra)
    started = time.time()
    status_view = window.active_view()
    result = {}
    turn = {"view": chat, "draft": ""}

    def open_turn():
        view = turn["view"] = _open_chat(window)
        turn["draft"] = _begin_turn(view)
        _block(view)
        _write(view, "## {}\n".format(title))
        if echo:
            _write(view, "> {}\n".format(echo.replace("\n", "\n> ")))

    def handle(kind, data):
        view = turn["view"]
        if kind == "result":
            result.update(data)
        elif quiet or view is None or not view.is_valid():
            return
        elif kind == "session":
            view.settings().set(K_SESSION, data)
        elif kind == "text_start":
            _block(view)
        elif kind == "text":
            _write(view, data)
        elif kind == "tool":
            _line(view)
            _write(view, "  ⚙ {}\n".format(data))
        elif kind == "raw":
            _line(view)
            _write(view, data + "\n")

    def edits_summary(view):
        if not backup:
            return ""
        diff, changed, truncated = core.edits_diff(backup, cwd)
        if not changed:
            shutil.rmtree(backup, ignore_errors=True)
            return ""
        core.mark_applied(backup)
        previous = view.settings().get(K_BACKUP)
        if previous and previous != backup:
            shutil.rmtree(previous, ignore_errors=True)
        view.settings().set(K_BACKUP, backup)
        return ("### Cambios en {} archivo{}\n```diff\n{}\n```\n{}"
                "Para deshacerlos: «Claude: Revertir la última edición».\n").format(
            len(changed), "" if len(changed) == 1 else "s", diff,
            "(El diff es más largo; se muestra el comienzo.)\n" if truncated else "")

    def close_turn(text):
        view = turn["view"]
        if view is not None and view.is_valid():
            summary = edits_summary(view)
            if summary:
                _block(view)
                _write(view, summary)
            _block(view)
            _write(view, text)
            _end_turn(view, turn["draft"])
            if not quiet:
                _save_history(view)

    def finish(code, stderr, stopped):
        _runners.pop(window.id(), None)
        if status_view is not None and status_view.is_valid():
            status_view.erase_status(STATUS_KEY)
        if stopped:
            close_turn("— detenido\n")
            window.status_message("Claude: detenido")
            return
        if code != 0 or result.get("is_error") or not result:
            if quiet:
                open_turn()
            detail = stderr or (result.get("result") if result.get("is_error") else "") or ""
            close_turn("**Error** (código {}){}\n".format(
                code, ": " + str(detail) if detail else ""))
            return
        close_turn("— listo ({:.0f} s)\n".format(time.time() - started))
        window.status_message("Claude: listo")
        if on_result:
            on_result(result.get("result") or "")

    if not quiet:
        if not chat_turn:
            open_turn()
        state = turn["view"].settings()
        state.set(K_CWD, cwd)
        if not resume:
            state.erase(K_SESSION)
        if context is not None:
            state.set(K_CONTEXT, context.signature())

    runner = core.Runner(
        args, cwd, prompt,
        on_event=lambda kind, data: sublime.set_timeout(lambda: handle(kind, data), 0),
        on_exit=lambda code, err, stopped: sublime.set_timeout(
            lambda: finish(code, err, stopped), 0))
    try:
        runner.start()
    except OSError as error:
        sublime.error_message("No se pudo ejecutar Claude Code:\n\n{}".format(error))
        close_turn("**Error**: no se pudo ejecutar Claude Code.\n")
        return
    _runners[window.id()] = runner
    if status_view is not None:
        status_view.set_status(STATUS_KEY, "Claude: trabajando…")


def _chat_tools(view):
    settings = _settings()
    if view.settings().get(K_EDIT):
        return settings.get("edit_tools", core.EDIT_TOOLS), "acceptEdits"
    return settings.get("read_tools", core.READ_TOOLS), None


class ClaudeChatCommand(sublime_plugin.WindowCommand):
    """Abre el chat y deja el cursor listo para escribir."""

    def run(self):
        view = _open_chat(self.window)
        if view.settings().get(K_START) is not None:
            view.sel().clear()
            view.sel().add(sublime.Region(view.size(), view.size()))
            view.show(view.size())


class ClaudeChatSendCommand(sublime_plugin.TextCommand):
    """Envía lo escrito después de la marca; sigue la conversación si hay una."""

    def run(self, edit):
        view, window = self.view, self.view.window()
        state = view.settings()
        start = state.get(K_START)
        if window is None:
            return
        if start is None:
            window.status_message("Claude sigue trabajando; usa «Claude: Detener» para cortarlo.")
            return
        message = view.substr(sublime.Region(start, view.size())).strip()
        if not message:
            return
        state.erase(K_START)
        view.set_read_only(True)

        context = _Context(window)
        session = state.get(K_SESSION)
        cwd = state.get(K_CWD) if session and state.get(K_CWD) else context.cwd
        prompt = message
        described = context.describe()
        if described and (not session or context.signature() != state.get(K_CONTEXT)):
            # El contexto sólo viaja cuando cambió desde el último mensaje.
            prompt = "{}\n\n{}".format(message, described)
        attached = core.mentioned_files(message, cwd)
        if attached:
            prompt += "\n\nArchivos adjuntos (léelos antes de responder): {}".format(
                ", ".join("`{}`".format(path) for path in attached))
        notice = state.get(K_NOTICE)
        if notice:
            state.erase(K_NOTICE)
            if session:
                prompt = "({})\n\n{}".format(notice, prompt)
        tools, mode = _chat_tools(view)
        _ask(window, "", prompt, cwd, tools, mode=mode, resume=session,
             context=context, chat_turn=True)


class ClaudeChatReplaceCommand(sublime_plugin.TextCommand):
    def run(self, edit, a, b, text):
        self.view.replace(edit, sublime.Region(a, b), text)


class ClaudeNewChatCommand(sublime_plugin.WindowCommand):
    """Vacía el chat y empieza una conversación nueva."""

    def run(self):
        if self.window.id() in _runners:
            self.window.status_message("Claude sigue trabajando; detenlo antes de limpiar.")
            return
        view = _open_chat(self.window)
        state = view.settings()
        for key in (K_START, K_SESSION, K_CONTEXT, K_CWD, K_NOTICE):
            state.erase(key)
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {"a": 0, "b": view.size(), "text": ""})
        _end_turn(view)


class ClaudeToggleEditCommand(sublime_plugin.WindowCommand):
    """Decide si lo que se pide desde el chat puede modificar archivos."""

    def run(self):
        view = _open_chat(self.window, focus=False)
        enabled = not view.settings().get(K_EDIT)
        _set_edit(view, enabled)
        self.window.status_message(
            "Claude: el chat puede editar archivos" if enabled
            else "Claude: el chat es de sólo lectura")


class ClaudeReviewFileCommand(sublime_plugin.WindowCommand):
    """Revisa el archivo actual, o sólo la selección si la hay."""

    def run(self):
        context = _Context(self.window)
        view = context.view
        if view is None or view.size() == 0:
            self.window.status_message("Claude: no hay nada que revisar en esta pestaña.")
            return
        name = "`{}`".format(context.rel) if context.rel else "el búfer abierto"
        if context.region is not None:
            target = "las líneas seleccionadas de {}".format(name)
        else:
            target = "el archivo {}".format(name) if context.rel else name
        _ask(self.window, "Revisión · {}".format(context.rel or "búfer sin guardar"),
             _with_context(REVIEW_FILE.format(target=target), context), context.cwd,
             _settings().get("read_tools", core.READ_TOOLS), context=context)


class ClaudeReviewChangesCommand(sublime_plugin.WindowCommand):
    """Revisa los cambios sin confirmar del repositorio (git diff HEAD)."""

    def run(self):
        context = _Context(self.window)
        try:
            status, diff, truncated = core.git_changes(context.cwd)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            sublime.error_message("No se pudieron leer los cambios con git:\n\n{}".format(error))
            return
        if not status.strip():
            self.window.status_message("Claude: no hay cambios sin confirmar.")
            return
        prompt = "{}\n\n<git_status>\n{}</git_status>\n\n<git_diff{}>\n{}</git_diff>".format(
            REVIEW_CHANGES, status, ' recortado="si"' if truncated else "", diff)
        _ask(self.window, "Revisión · cambios sin confirmar", prompt, context.cwd,
             _settings().get("read_tools", core.READ_TOOLS))


class ClaudeExplainBuildCommand(sublime_plugin.WindowCommand):
    """Explica los errores del último build (panel «exec»)."""

    def run(self):
        panel = self.window.find_output_panel("exec")
        text = panel.substr(sublime.Region(0, panel.size())).strip() if panel else ""
        if not text:
            self.window.status_message("Claude: no hay salida de build que revisar.")
            return
        context = _Context(self.window)
        prompt = "{}\n\n<salida>\n{}\n</salida>".format(EXPLAIN_BUILD, text[-BUFFER_LIMIT:])
        _ask(self.window, "Errores del build", prompt, context.cwd,
             _settings().get("read_tools", core.READ_TOOLS))


def _saved_context(window, action):
    """Contexto de un archivo guardado en disco, que es lo que Claude va a leer y editar."""
    context = _Context(window)
    if context.view is None or not context.path:
        sublime.error_message("Guarda el archivo antes de {}.".format(action))
        return None
    if context.dirty:
        context.view.run_command("save", {"async": False})
        context = _Context(window)
    return context


def _ask_edit(window, title, prompt, context, echo=None):
    # Quien pide una edición espera poder seguir pidiéndolas desde el chat.
    _set_edit(_open_chat(window, focus=False), True)
    _ask(window, title, prompt, context.cwd,
         _settings().get("edit_tools", core.EDIT_TOOLS), mode="acceptEdits",
         echo=echo, context=context)


def _background(function):
    threading.Thread(target=function, daemon=True).start()


class ClaudeEditFileCommand(sublime_plugin.WindowCommand):
    """Claude modifica el archivo actual en disco según una instrucción."""

    def run(self):
        view = _file_view(self.window)
        if view is None or not view.file_name():
            sublime.error_message("Guarda el archivo antes de pedirle a Claude que lo edite.")
            return
        self.window.show_input_panel("Qué debe cambiar Claude:", "", self.on_done, None, None)

    def on_done(self, instruction):
        instruction = instruction.strip()
        if not instruction:
            return
        context = _saved_context(self.window, "pedirle a Claude que lo edite")
        if context is None:
            return
        prompt = EDIT_FILE.format(rel=context.rel, instruction=instruction)
        if context.region is not None:
            prompt = _with_context(prompt, context)
        _ask_edit(self.window, "Edición · {}".format(context.rel), prompt, context,
                  echo=instruction)


def _lsp_diagnostics(view, rel):
    """Avisos que LSP tiene para el archivo, o None si LSP no lo está atendiendo.

    Usa partes internas del paquete LSP, así que cualquier sorpresa se trata como
    «no disponible» y se recurre a ruff.
    """
    try:
        from LSP.plugin.core.registry import windows
        from LSP.plugin.core.url import view_to_uri
        manager = windows.lookup(view.window())
        if manager is None:
            return None
        uri = view_to_uri(view)
        lines, sessions = [], 0
        for session in manager.get_sessions():
            sessions += 1
            # Severidad 4 son pistas de estilo (variables sin usar atenuadas): no van.
            for item in session.diagnostics.get_diagnostics_for_uri(uri, 3):
                start = item["range"]["start"]
                label = " ".join(str(part) for part in (item.get("source"), item.get("code"))
                                 if part not in (None, ""))
                lines.append("{}:{}:{}: {}{}".format(
                    rel, start["line"] + 1, start["character"] + 1,
                    "[{}] ".format(label) if label else "",
                    " ".join(str(item.get("message", "")).split())))
        return lines if sessions else None
    except Exception:
        return None


class ClaudeFixDiagnosticsCommand(sublime_plugin.WindowCommand):
    """Corrige lo que marcan LSP (pyright, ruff) o, si no está, ruff por su cuenta."""

    def run(self):
        context = _saved_context(self.window, "corregir sus avisos")
        if context is None:
            return
        diagnostics = _lsp_diagnostics(context.view, context.rel)
        source = "LSP"
        if not diagnostics:
            ruff = core.find_ruff(context.cwd, _settings().get("ruff_path"))
            if ruff is None and diagnostics is None:
                sublime.error_message(
                    "No hay de dónde sacar los avisos: LSP no está atendiendo este archivo "
                    "y no se encontró ruff.")
                return
            if ruff is not None:
                source = "ruff"
                try:
                    diagnostics = core.ruff_diagnostics(ruff, context.path, context.cwd)
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                    sublime.error_message("ruff falló:\n\n{}".format(error))
                    return
        if not diagnostics:
            self.window.status_message("Claude: el linter no marca nada en este archivo.")
            return
        prompt = FIX_DIAGNOSTICS.format(rel=context.rel, diagnostics="\n".join(diagnostics))
        _ask_edit(self.window, "Avisos del linter · {}".format(context.rel), prompt, context,
                  echo="{} aviso{} de {}".format(
                      len(diagnostics), "" if len(diagnostics) == 1 else "s", source))


class ClaudeWriteTestsCommand(sublime_plugin.WindowCommand):
    """Escribe pruebas pytest para la selección o para el archivo entero."""

    def run(self):
        context = _saved_context(self.window, "generar sus pruebas")
        if context is None:
            return
        target = "el código seleccionado" if context.region is not None else "el módulo"
        prompt = WRITE_TESTS.format(target=target, rel=context.rel)
        if context.region is not None:
            prompt = _with_context(prompt, context)
        _ask_edit(self.window, "Pruebas · {}".format(context.rel), prompt, context)


class ClaudeCommitMessageCommand(sublime_plugin.WindowCommand):
    """Propone el mensaje de commit de lo preparado (o de todo, si no hay nada preparado)."""

    def run(self):
        context = _Context(self.window)
        try:
            diff, staged, subjects = core.commit_context(context.cwd)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            sublime.error_message("No se pudieron leer los cambios con git:\n\n{}".format(error))
            return
        if not diff.strip():
            self.window.status_message("Claude: no hay cambios para describir.")
            return

        def show(message):
            message = core.strip_fences(message).strip()
            if not message:
                self.window.status_message("Claude no devolvió un mensaje.")
                return
            sublime.set_clipboard(message)
            _note(self.window, "## Mensaje de commit\n{}; quedó copiado al portapapeles.\n\n"
                               "```\n{}\n```\n".format(
                                   "Describe lo preparado con `git add`" if staged
                                   else "No hay nada preparado: describe todos los cambios",
                                   message))

        _ask(self.window, "Mensaje de commit",
             COMMIT_MESSAGE.format(subjects=subjects, diff=diff), context.cwd,
             core.NO_TOOLS, quiet=True, on_result=show)


class ClaudeExplainClipboardCommand(sublime_plugin.WindowCommand):
    """Explica el error o traceback que está copiado en el portapapeles."""

    def run(self):
        text = sublime.get_clipboard().strip()
        if not text:
            self.window.status_message("Claude: el portapapeles está vacío.")
            return
        context = _Context(self.window)
        last = text.splitlines()[-1].strip()
        prompt = "{}\n\n<error>\n{}\n</error>".format(EXPLAIN_ERROR, text[-BUFFER_LIMIT:])
        _ask(self.window, "Error del portapapeles", prompt, context.cwd,
             _settings().get("read_tools", core.READ_TOOLS),
             echo=last if len(last) <= 200 else last[:197] + "...")


class ClaudeRemoteLogsCommand(sublime_plugin.WindowCommand):
    """Trae los logs de un servicio del servidor y le pide a Claude un diagnóstico."""

    def run(self):
        settings = _settings()
        self.host = settings.get("remote_host")
        self.ssh_args = settings.get("remote_ssh_args")
        if not self.host:
            sublime.error_message(
                "Indica el servidor en «remote_host» (Preferences: Claude Settings).")
            return
        listing = settings.get("remote_list_command") or REMOTE_LIST
        self.window.status_message("Claude: consultando {}…".format(self.host))
        self.remote(lambda: core.parse_services(core.ssh_run(self.host, listing, self.ssh_args)),
                    self.choose)

    def remote(self, work, then):
        """Corre la consulta fuera del hilo principal: SSH puede tardar."""
        def task():
            try:
                value = work()
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                message = str(error)
                sublime.set_timeout(lambda: sublime.error_message(
                    "No se pudo consultar {}:\n\n{}".format(self.host, message)), 0)
                return
            sublime.set_timeout(lambda: then(value), 0)
        _background(task)

    def choose(self, services):
        if not services:
            self.window.status_message("Claude: no hay servicios en ejecución en el servidor.")
            return
        self.services = services
        self.window.show_quick_panel([[name, status] for name, status in services], self.fetch)

    def fetch(self, index):
        if index < 0:
            return
        settings = _settings()
        name = self.services[index][0]
        lines = int(settings.get("remote_log_lines") or 200)
        template = settings.get("remote_logs_command") or REMOTE_LOGS
        self.window.status_message("Claude: leyendo los logs de {}…".format(name))
        self.remote(
            lambda: core.ssh_run(self.host, core.logs_command(template, name, lines),
                                 self.ssh_args),
            lambda logs: self.diagnose(name, lines, logs))

    def diagnose(self, name, lines, logs):
        if not logs.strip():
            self.window.status_message("Claude: {} no tiene registros.".format(name))
            return
        context = _Context(self.window)
        prompt = "{}\n\n<logs>\n{}\n</logs>".format(
            REMOTE_DIAGNOSE.format(lines=lines, name=name, host=self.host),
            logs[-BUFFER_LIMIT:])
        _ask(self.window, "Logs · {}".format(name), prompt, context.cwd,
             _settings().get("read_tools", core.READ_TOOLS))


class ClaudeRemoteStatusCommand(ClaudeRemoteLogsCommand):
    """Muestra qué servicios del servidor están caídos o reiniciándose. No usa a Claude."""

    def choose(self, services):
        problems, healthy = core.service_report(services)
        lines = ["## Servidor · {}".format(self.host)]
        lines += ["⚠ {} — {}".format(name, status or "sin estado") for name, status in problems]
        if healthy:
            lines.append("✓ {} en marcha: {}".format(
                len(healthy), ", ".join(name for name, _ in healthy)))
        if not services:
            lines.append("No hay servicios.")
        if problems:
            lines.append("Para ver por qué: «Claude: Diagnosticar logs del servidor…».")
        _note(self.window, "\n".join(lines) + "\n")


class ClaudeRunTestsCommand(sublime_plugin.WindowCommand):
    """Corre pytest sobre las pruebas del archivo y, si fallan, se las pasa a Claude."""

    def run(self):
        context = _saved_context(self.window, "correr sus pruebas")
        if context is None:
            return
        if not context.path.endswith(".py"):
            self.window.status_message("Claude: este comando corre pytest, sólo para Python.")
            return
        if core.is_test_file(context.path):
            target = context.path
        else:
            target = core.find_tests_for(context.path, context.cwd)
        if target is None:
            name = os.path.splitext(os.path.basename(context.path))[0]
            sublime.error_message(
                "No encontré pruebas para {0}.py (busqué test_{0}.py y {0}_test.py).\n\n"
                "Se pueden crear con «Claude: Generar pruebas (pytest)».".format(name))
            return
        python = core.find_project_python(context.cwd)
        if python is None:
            sublime.error_message("No se encontró un Python con el que correr pytest.")
            return
        if self.window.id() in _testing:
            self.window.status_message("Claude: ya hay pruebas corriendo.")
            return
        _testing.add(self.window.id())
        target = os.path.relpath(target, context.cwd)
        timeout = int(_settings().get("test_timeout") or 300)
        self.window.status_message("Claude: corriendo pytest {}…".format(target))

        def task():
            try:
                code, output = core.run_tests(python, target, context.cwd, timeout)
            except subprocess.TimeoutExpired:
                code, output = None, "pytest no terminó en {} s.".format(timeout)
            except OSError as error:
                code, output = None, str(error)
            sublime.set_timeout(lambda: self.done(context, target, code, output), 0)

        _background(task)

    def done(self, context, target, code, output):
        _testing.discard(self.window.id())
        summary = core.last_line(output)
        if code is None:
            sublime.error_message("No se pudieron correr las pruebas:\n\n{}".format(output))
        elif code == 0:
            _note(self.window, "## Pruebas · {}\n— pasan: {}\n".format(target, summary))
        elif code == 5:
            self.window.status_message("Claude: pytest no encontró pruebas en {}.".format(target))
        else:
            prompt = FIX_TESTS.format(command="pytest " + target, output=output[-BUFFER_LIMIT:])
            _ask_edit(self.window, "Pruebas que fallan · {}".format(target), prompt, context,
                      echo=summary)


class ClaudeAttachFileCommand(sublime_plugin.WindowCommand):
    """Elige un archivo del proyecto y lo deja escrito como @ruta en el mensaje."""

    def run(self):
        chat = _open_chat(self.window)
        state = chat.settings()
        if state.get(K_START) is None:
            self.window.status_message("Claude sigue trabajando; espera a que termine.")
            return
        context = _Context(self.window)
        cwd = state.get(K_CWD) if state.get(K_SESSION) and state.get(K_CWD) else context.cwd
        files = core.project_files(cwd)
        if not files:
            self.insert(chat, "@")
            return
        self.window.show_quick_panel(
            files, lambda index: self.insert(
                chat, "@" if index < 0 else "@{} ".format(files[index])))

    def insert(self, chat, text):
        start = chat.settings().get(K_START)
        if not chat.is_valid() or start is None:
            return
        point = chat.sel()[0].begin() if len(chat.sel()) else chat.size()
        if point < start:
            point = chat.size()
        chat.run_command("claude_chat_replace", {"a": point, "b": point, "text": text})
        chat.sel().clear()
        chat.sel().add(sublime.Region(point + len(text), point + len(text)))
        self.window.focus_view(chat)


class ClaudeHistoryCommand(sublime_plugin.WindowCommand):
    """Retoma una conversación anterior: vuelve su texto al chat y sigue esa sesión."""

    def run(self):
        entries = core.history_list(_history_dir())
        if not entries:
            self.window.status_message("Claude: todavía no hay conversaciones guardadas.")
            return
        now = time.time()
        items = [[entry.get("title") or "Conversación", "{} · {}".format(
            core.age_text(now - entry["updated"]),
            os.path.basename(entry.get("cwd") or "") or "sin carpeta")] for entry in entries]
        self.window.show_quick_panel(items, lambda index: self.load(entries, index))

    def load(self, entries, index):
        if index < 0:
            return
        if self.window.id() in _runners:
            self.window.status_message("Claude sigue trabajando; detenlo antes de cambiar de chat.")
            return
        entry = entries[index]
        view = _open_chat(self.window)
        state = view.settings()
        for key in (K_START, K_CONTEXT, K_NOTICE):
            state.erase(key)
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {"a": 0, "b": view.size(), "text": entry["text"]})
        state.set(K_SESSION, entry["session"])
        state.set(K_CWD, entry.get("cwd"))
        _set_edit(view, entry.get("edit"))
        _end_turn(view)


class ClaudeSelectModelCommand(sublime_plugin.WindowCommand):
    """Elige el modelo de las próximas consultas."""

    def run(self):
        settings = _settings()
        current = settings.get("model") or ""
        items = [["{}{}".format("● " if model == current else "", name), detail]
                 for model, name, detail in MODELS]
        if current and current not in [model for model, _, _ in MODELS]:
            items.append(["● {}".format(current), "El que está en los ajustes"])
        self.window.show_quick_panel(items, self.choose)

    def choose(self, index):
        if index < 0 or index >= len(MODELS):
            return
        model, name, _ = MODELS[index]
        settings = _settings()
        settings.set("model", model)
        sublime.save_settings(SETTINGS)
        self.window.status_message("Claude: modelo {}".format(name))


class ClaudeRevertEditCommand(sublime_plugin.WindowCommand):
    """Devuelve los archivos a como estaban antes de la última edición de Claude."""

    def run(self):
        chat = _find_chat(self.window)
        backup = chat.settings().get(K_BACKUP) if chat is not None else None
        if not backup or not os.path.isdir(backup):
            self.window.status_message("Claude: no hay una edición que revertir.")
            return
        if self.window.id() in _runners:
            self.window.status_message("Claude sigue trabajando; detenlo antes de revertir.")
            return
        changed = core.changed_since(backup)
        if changed and not sublime.ok_cancel_dialog(
                "Estos archivos cambiaron después de la edición de Claude:\n\n{}\n\n"
                "Si reviertes, esos cambios posteriores se pierden.".format(
                    "\n".join(os.path.basename(path) for path in changed)),
                "Revertir igual"):
            return
        try:
            restored = core.revert_edits(backup)
        except OSError as error:
            sublime.error_message("No se pudo revertir:\n\n{}".format(error))
            return
        names = ", ".join(os.path.basename(path) for path in restored) or "nada que cambiar"
        chat.settings().erase(K_BACKUP)
        shutil.rmtree(backup, ignore_errors=True)
        # Claude no ve el disco entre mensajes: hay que contárselo en el siguiente.
        chat.settings().set(
            K_NOTICE, "Aviso: el usuario revirtió tu última edición ({}).".format(names))
        _note(self.window, "— edición revertida: {}\n".format(names))

    def is_enabled(self):
        chat = _find_chat(self.window)
        return chat is not None and bool(chat.settings().get(K_BACKUP))


class ClaudeWriteCodeCommand(sublime_plugin.WindowCommand):
    """Genera código y lo inserta en el cursor o reemplaza la selección."""

    def run(self):
        if _file_view(self.window) is None:
            return
        self.window.show_input_panel("Código a escribir:", "", self.on_done, None, None)

    def on_done(self, instruction):
        instruction = instruction.strip()
        view = _file_view(self.window)
        if not instruction or view is None or len(view.sel()) == 0:
            return
        context = _Context(self.window)
        region = view.sel()[0]
        begin, end = region.begin(), region.end()
        head, selected, tail, truncated = core.clip_around(
            view.substr(sublime.Region(0, view.size())), begin, end)
        body = "<antes>\n{}</antes>\n".format(head)
        if selected:
            body += "<seleccion_a_reemplazar>\n{}</seleccion_a_reemplazar>\n".format(selected)
        body += "<despues>\n{}</despues>".format(tail)
        if truncated:
            body += "\n(El archivo es más largo; sólo se muestra lo que rodea al cursor.)"
        prompt = WRITE_CODE.format(
            action="reemplazar la selección" if selected else "insertar en la posición del cursor",
            name=context.rel or "un búfer sin guardar",
            syntax=" ({})".format(context.syntax) if context.syntax else "",
            instruction=instruction, body=body)
        changes = view.change_count()

        def insert(text):
            code = core.strip_fences(text)
            if not code.strip():
                self.window.status_message("Claude no devolvió código.")
                return
            if view.is_valid() and view.change_count() == changes:
                view.run_command("claude_replace_region", {"a": begin, "b": end, "text": code})
                return
            # El búfer cambió mientras tanto: las posiciones ya no valen.
            _note(self.window, "## Código generado\nEl archivo cambió mientras Claude "
                               "escribía, así que no se insertó:\n\n```\n{}\n```\n".format(code))

        _ask(self.window, "Escribir código", prompt, context.cwd, core.NO_TOOLS,
             quiet=True, on_result=insert)


class ClaudeReplaceRegionCommand(sublime_plugin.TextCommand):
    def run(self, edit, a, b, text):
        self.view.replace(edit, sublime.Region(a, b), text)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(a, a + len(text)))
        self.view.show(a)


class ClaudeStopCommand(sublime_plugin.WindowCommand):
    def run(self):
        runner = _runners.get(self.window.id())
        if runner is not None:
            runner.stop()

    def is_enabled(self):
        return self.window.id() in _runners


def _has_terminus():
    return any(name == "Terminus" or name.startswith("Terminus.") for name in sys.modules)


class ClaudeTerminalCommand(sublime_plugin.WindowCommand):
    """Abre Claude Code interactivo en una terminal, en la carpeta del proyecto."""

    def run(self, with_file=False):
        settings = _settings()
        claude = core.find_claude(settings.get("claude_path"))
        terminus = settings.get("terminal_mode", "auto") != "external" and _has_terminus()
        terminal = core.find_terminal(settings.get("terminal"))
        if claude is None or (terminal is None and not terminus):
            sublime.error_message(
                "No se encontró {}.\n\nIndica su ruta en Preferences: Claude Settings.".format(
                    "el ejecutable de Claude Code" if claude is None else "una terminal"))
            return
        context = _Context(self.window)
        prompt = None
        if with_file:
            if not context.rel:
                sublime.error_message("Guarda el archivo antes de abrirlo con Claude.")
                return
            prompt = "Voy a trabajar en @{}. Léelo y espera mis instrucciones.".format(context.rel)
        if terminus:
            # Con Terminus la sesión interactiva queda en una pestaña del editor.
            self.window.run_command("terminus_open", {
                "cmd": [claude] + ([prompt] if prompt else []),
                "cwd": context.cwd, "title": "Claude Code"})
            return
        script = core.terminal_script(
            claude, context.cwd, prompt,
            directory=os.path.join(sublime.cache_path(), "ClaudeCode"))
        command = [terminal] + list(settings.get("terminal_args") or ["-e"]) + [script]
        try:
            subprocess.Popen(command, cwd=context.cwd, start_new_session=True)
        except OSError as error:
            sublime.error_message("No se pudo abrir la terminal:\n\n{}".format(error))


# --- Eventos ----------------------------------------------------------------

class ClaudeFileTracker(sublime_plugin.EventListener):
    """Recuerda la última pestaña de trabajo para usarla como contexto desde el chat."""

    def on_activated(self, view):
        window = view.window()
        if window is None or _is_chat(view) or view.element() is not None:
            return
        _last_file[window.id()] = view.id()


def _restore(view):
    """Devuelve la conversación a como estaba, deshaciendo la edición que la tocó."""
    try:
        start = view.settings().get(K_START)
        expected = _transcripts.get(view.id())
        if not view.is_valid() or start is None or expected is None:
            return
        if view.substr(sublime.Region(0, start)) != expected:
            view.run_command("undo")
        if view.substr(sublime.Region(0, start)) != expected:
            # No había nada que deshacer: se reescribe, aunque se pierda el borrador.
            view.run_command("claude_chat_replace", {"a": 0, "b": view.size(), "text": expected})
            view.sel().clear()
            view.sel().add(sublime.Region(view.size(), view.size()))
    finally:
        # La guarda sigue puesta mientras se restaura: esas ediciones no se vigilan.
        _restoring.discard(view.id())


class ClaudeChatListener(sublime_plugin.ViewEventListener):
    """Protege la conversación: sólo se conserva lo escrito después de la marca.

    No se usa el modo de sólo lectura según dónde esté el cursor: basta un evento
    de selección perdido para que la pestaña quede bloqueada y no deje escribir.
    """

    @classmethod
    def is_applicable(cls, settings):
        return bool(settings.get(CHAT))

    def on_modified(self):
        view = self.view
        start = view.settings().get(K_START)
        if start is None or view.id() in _restoring:
            return
        current = view.substr(sublime.Region(0, start))
        expected = _transcripts.setdefault(view.id(), current)
        if current != expected:
            _restoring.add(view.id())
            sublime.set_timeout(lambda: _restore(view), 0)

    def on_text_command(self, name, args):
        if name in HISTORY_COMMANDS and self.view.settings().get(K_START) is None:
            # Mientras Claude responde, deshacer se llevaría trozos de la respuesta.
            return ("claude_noop", {})
        start = self.view.settings().get(K_START)
        backwards = name == "left_delete" or (
            name == "delete_word" and not (args or {}).get("forward"))
        if backwards and start is not None and all(
                region.empty() and region.begin() == start for region in self.view.sel()):
            # Borrar hacia atrás al inicio del mensaje no debe comerse la marca;
            # deshacerlo después devolvería también lo recién borrado.
            return ("claude_noop", {})
        return None

    def on_post_text_command(self, name, args):
        """Doble clic sobre una cita `ruta:línea` abre el archivo."""
        if name != "drag_select" or (args or {}).get("by") != "words":
            return
        view, window = self.view, self.view.window()
        if window is None or len(view.sel()) == 0:
            return
        point = view.sel()[0].begin()
        line = view.line(point)
        offset = point - line.begin()
        for match in REFERENCE.finditer(view.substr(line)):
            if match.start() <= offset <= match.end():
                path = os.path.join(view.settings().get(K_CWD) or "", match.group(1))
                if os.path.isfile(path):
                    target = _file_view(window)
                    group = window.get_view_index(target)[0] if target is not None else 0
                    window.open_file("{}:{}".format(path, match.group(2)),
                                     sublime.ENCODED_POSITION, group=max(group, 0))
                return

    def on_pre_close(self):
        _transcripts.pop(self.view.id(), None)
        window = self.view.window()
        runner = _runners.get(window.id()) if window is not None else None
        if runner is not None:
            runner.stop()


def plugin_loaded():
    # Un chat restaurado al abrir Sublime pudo quedar a medio responder: se le
    # devuelve la marca para que se pueda seguir escribiendo.
    for window in sublime.windows():
        view = _find_chat(window)
        if view is None or window.id() in _runners:
            continue
        start = view.settings().get(K_START)
        intact = (start is not None and start <= view.size()
                  and view.substr(sublime.Region(start - len(MARK), start)) == MARK)
        if not intact:
            view.settings().erase(K_START)
            _end_turn(view)


def plugin_unloaded():
    for runner in list(_runners.values()):
        runner.stop()

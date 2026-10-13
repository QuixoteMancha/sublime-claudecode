"""Claude Code dentro de Sublime Text: un chat en una pestaña, edición y terminal."""
import html
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
K_PLAN = "claude_plan"
K_NUMBER = "claude_number"
K_LIMIT = "claude_limit"
K_NOTE = "claude_mode_note"
K_BACKUPS = "claude_edits"

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks", "snapshot.py")
REMOTE_LIST = "docker ps -a --format '{{.Names}}\\t{{.Status}}'"
REMOTE_LOGS = "docker logs --tail {lines} {name} 2>&1"

REFERENCE = re.compile(r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+):(\d+)")
SLASH = re.compile(r"^/[\w:-]+(\s|$)")  # un comando de Claude Code, no una ruta
BACKUPS_KEPT = 10
TABS_LIMIT = 12
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
    "modifiques el código que se prueba: si encuentras un bug, déjalo anotado. Al final da "
    "el comando para correrlas."
)
COMMIT_MESSAGE = (
    "Escribe el mensaje de commit para estos cambios. Sigue el idioma y el estilo de los "
    "últimos commits del repositorio. Primera línea de 72 caracteres como máximo; si hace "
    "falta, un cuerpo breve que explique el porqué. Responde únicamente con el mensaje, "
    "sin comillas ni bloque ```.\n\n<ultimos_commits>\n{subjects}</ultimos_commits>\n\n"
    "<diff>\n{diff}</diff>"
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
    "resume qué cambiaste; las pruebas las vuelvo a correr yo."
)
MODELS = (
    ("", "Predeterminado", "El que tenga configurado Claude Code"),
    ("fable", "Fable", "El de mayor alcance: problemas largos y difíciles"),
    ("opus", "Opus", "El más capaz: revisiones y cambios delicados"),
    ("sonnet", "Sonnet", "Equilibrado: el trabajo de todos los días"),
    ("haiku", "Haiku", "El más rápido: preguntas cortas"),
)
EFFORTS = (
    ("", "Predeterminado", "El que tenga configurado Claude Code"),
    ("low", "Bajo", "Preguntas cortas y cambios mecánicos"),
    ("medium", "Medio", "El trabajo de todos los días"),
    ("high", "Alto", "Razona más antes de responder"),
    ("xhigh", "Muy alto", "Bugs difíciles y cambios que cruzan varios archivos"),
    ("max", "Máximo", "Lo más a fondo que puede; tarda y gasta más"),
)
WRITE_CODE = (
    "Escribe código para {action} en `{name}`{syntax}.\n\n"
    "Instrucción: {instruction}\n\n"
    "{body}\n\n"
    "Responde únicamente con el código que va en ese lugar, con la indentación que "
    "corresponde: sin explicaciones y sin bloque ```."
)

_runners = {}    # «v<id del chat>» o «w<id de ventana>» (consultas sin chat) -> consulta en curso
_sessions = {}   # id del chat -> proceso de Claude que sigue vivo entre mensajes
_queued = {}     # id del chat -> mensajes que esperan a que termine la respuesta
_live_off = []   # por qué se dejó de usar el proceso persistente, si falló al arrancar
_last_file = {}  # id de ventana -> id de la última pestaña que no es el chat
_last_chat = {}  # id de ventana -> id del último chat usado
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
            "plan": bool(state.get(K_PLAN)),
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

def _chats(window):
    return [view for view in window.views() if _is_chat(view)]


def _find_chat(window):
    """El chat en uso: el que tiene el foco o, si no, el último que lo tuvo."""
    chats = _chats(window)
    active = window.active_view()
    if active is not None and _is_chat(active):
        return active
    wanted = _last_chat.get(window.id())
    for view in chats:
        if view.id() == wanted:
            return view
    return chats[0] if chats else None


def _slot(window, view=None):
    return "v{}".format(view.id()) if view is not None else "w{}".format(window.id())


def _busy(view):
    return view is not None and _slot(None, view) in _runners


def _current_runner(window):
    """La consulta que «Detener» corta: la del chat en uso o, si no, la que haya."""
    chat = _find_chat(window)
    keys = ([_slot(window, chat)] if chat is not None else []) + [_slot(window)]
    keys += [_slot(window, view) for view in _chats(window)]
    for key in keys:
        if key in _runners:
            return _runners[key]
    return None


def _live_enabled():
    return bool(_settings().get("persistent_chat", True)) and not _live_off


def _drop_session(view_id):
    live = _sessions.pop(view_id, None)
    if live is not None:
        live["session"].close()
        if live.get("backup"):
            shutil.rmtree(live["backup"], ignore_errors=True)


def _create_chat(window):
    working = _last_file.get(window.id())
    number = 1 + max([chat.settings().get(K_NUMBER) or 1 for chat in _chats(window)] or [0])
    view = window.new_file()
    view.set_name("Claude")
    view.set_scratch(True)
    view.assign_syntax(SYNTAX)
    settings = view.settings()
    settings.set(CHAT, True)
    settings.set(K_NUMBER, number)
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
    _set_edit(view, _settings().get("chat_edit_default", False))
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


def _clear_activity(view, turn):
    """Borra la línea de actividad: acompaña la espera, no es parte de la respuesta."""
    line = turn.pop("activity", "")
    start = view.size() - len(line)
    if line and view.substr(sublime.Region(start, view.size())) == line:
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {"a": start, "b": view.size(), "text": ""})
        view.set_read_only(True)


def _show_activity(view, turn, text):
    line = "  ⋯ {}\n".format(text)
    old = turn.get("activity", "")
    start = view.size() - len(old)
    if old and view.substr(sublime.Region(start, view.size())) == old:
        # Se reescribe en su lugar: el contador avanza sin mover lo que se está leyendo.
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {"a": start, "b": view.size(), "text": line})
        view.set_read_only(True)
    else:
        _line(view)
        _write(view, line)
    turn["activity"] = line


def _diff_regions(view, start=0):
    text = view.substr(sublime.Region(start, view.size()))
    return [sublime.Region(start + a, start + b) for a, b, _, _ in core.diff_folds(text)]


def _script_regions(view):
    return [region for region in view.get_regions(SCRIPTS) if not region.empty()]


def _code_regions(view, start=0):
    text = view.substr(sublime.Region(start, view.size()))
    lines = int(_settings().get("fold_code_lines", 40) or 0)
    scripts = _script_regions(view)
    regions = [sublime.Region(start + a, start + b) for a, b in core.code_folds(text, lines)]
    # El guion de un permiso se pliega entero: no lleva además el pliegue de código largo.
    return [region for region in regions if not any(
        script.begin() <= region.begin() and region.end() <= script.end()
        for script in scripts)]


def _quote_regions(view, start=0):
    text = view.substr(sublime.Region(start, view.size()))
    return [sublime.Region(start + a, start + b) for a, b in core.quote_folds(text)]


FOLDS = "claude_folds"
SCRIPTS = "claude_scripts"  # guiones de un permiso, plegados: no se deducen del texto


def _foldable(view):
    """Todo lo que se puede plegar en el chat: diffs, código largo, citas y guiones."""
    return (_diff_regions(view) + _code_regions(view) + _quote_regions(view)
            + _script_regions(view))


def _fold_controls(view):
    """Pone bajo el encabezado de cada bloque plegable un control que lo abre o lo cierra."""
    if not view.is_valid():
        return
    view.erase_phantoms(FOLDS)
    if not _settings().get("fold_controls", True):
        return
    folded = {(region.begin(), region.end()) for region in view.folded_regions()}
    for region in _foldable(view):
        closed = (region.begin(), region.end()) in folded
        lines = view.substr(region).count("\n")
        label = "▸ mostrar {} línea{}".format(lines, "" if lines == 1 else "s") if closed \
            else "▾ plegar"
        view.add_phantom(
            FOLDS, sublime.Region(region.begin(), region.begin()),
            '<body id="claude-fold">{}<div class="fold"><a class="chip" href="x">{}</a></div>'
            '</body>'.format(BAR_STYLE, label),
            getattr(sublime, "LAYOUT_BLOCK", 2),
            lambda _, region=region, closed=closed: _toggle_fold(view, region, closed))


def _toggle_fold(view, region, closed):
    # Manda lo que decía el control: si el bloque ya se abrió desde la marca «…», el
    # clic sólo pone el control al día.
    if closed:
        view.unfold(region)
    else:
        view.fold(region)
    _fold_controls(view)


def _fold_diffs(view, start=0):
    """Pliega cada archivo de los diffs: queda el encabezado y se despliega a pedido."""
    regions = _diff_regions(view, start)
    if regions and _settings().get("fold_edits", True):
        view.fold(regions)


def _fold_code(view, start=0):
    """Pliega el código largo de una respuesta, dejando a la vista sus primeras líneas."""
    regions = _code_regions(view, start)
    if regions:
        view.fold(regions)


def _fold_quotes(view, start=0):
    """Pliega las salidas de herramientas y el razonamiento: queda su encabezado."""
    regions = _quote_regions(view, start)
    if regions:
        view.fold(regions)
        _fold_controls(view)


def _backups(view):
    """Respaldos de las ediciones del chat que se pueden revertir, del más viejo al último."""
    state = view.settings()
    stack = [path for path in state.get(K_BACKUPS) or [] if os.path.isdir(path)]
    last = state.get(K_BACKUP)
    if last and last not in stack and os.path.isdir(last):
        stack.append(last)
    return stack


def _set_backups(view, stack):
    state = view.settings()
    for old in stack[:-BACKUPS_KEPT]:
        shutil.rmtree(old, ignore_errors=True)
    stack = stack[-BACKUPS_KEPT:]
    state.set(K_BACKUPS, stack)
    if stack:
        state.set(K_BACKUP, stack[-1])
    else:
        state.erase(K_BACKUP)


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
        view.erase_phantoms(BAR)
        start = min(start, view.size())
        draft = view.substr(sublime.Region(start, view.size()))
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {
            "a": max(0, start - len(MARK)), "b": view.size(), "text": ""})
    view.set_read_only(True)
    return draft


# --- Controles desplegables ---------------------------------------------------

BAR = "claude_bar"
BAR_STYLE = (
    "<style>"
    "div.bar { padding: 0.2rem 0 0.3rem 0; font-size: 0.9rem; }"
    "div.item { padding: 0.15rem 0.3rem; }"
    "div.fold { padding: 0.1rem 0 0.2rem 0; font-size: 0.85rem; }"
    "a { text-decoration: none; color: var(--foreground); }"
    "a.chip { background-color: color(var(--foreground) alpha(0.1)); "
    "padding: 0.1rem 0.5rem; border-radius: 0.3rem; }"
    "span.detail { color: color(var(--foreground) alpha(0.55)); font-size: 0.85rem; }"
    "</style>"
)


def _controls(view):
    """Los desplegables del chat: (clave, etiqueta, [(nombre, detalle)], opción vigente)."""
    settings, state = _settings(), view.settings()
    mode = 2 if state.get(K_EDIT) else 1 if state.get(K_PLAN) else 0
    controls = [("mode", ("Sólo lectura", "◇ Plan", "✎ Edición")[mode],
                 [(name, detail) for name, detail, _ in ClaudeChatModeCommand.MODES], mode)]
    for key, title, options in (("model", "Modelo", MODELS), ("effort", "Esfuerzo", EFFORTS)):
        value = settings.get(key) or ""
        values = [option[0] for option in options]
        current = values.index(value) if value in values else -1
        name = options[current][1].lower() if current >= 0 else value
        controls.append((key, "{}: {}".format(title, name),
                         [(name, detail) for _, name, detail in options], current))
    return controls


def _bar_point(view):
    """Dónde va la barra: en la línea en blanco que precede a la marca."""
    start = view.settings().get(K_START) or 0
    return max(0, start - len(MARK) - 1)


def _show_bar(view):
    """Pone (o quita) la barra de controles sobre la marca donde se escribe."""
    if not view.is_valid():
        return
    view.erase_phantoms(BAR)
    if view.settings().get(K_START) is None or not _settings().get("chat_controls", True):
        return
    point = _bar_point(view)
    chips = "&nbsp;&nbsp;".join(
        '<a class="chip" href="{}">{} ▾</a>'.format(key, html.escape(label))
        for key, label, _, _ in _controls(view))
    state = view.settings()
    if state.get(K_PLAN) and state.get(K_SESSION):
        # Hay un plan sobre la mesa: un clic pasa a edición y pide aplicarlo.
        chips += '&nbsp;&nbsp;<a class="chip" href="apply">▶ Aplicar el plan</a>'
    view.add_phantom(
        BAR, sublime.Region(point, point),
        '<body id="claude-bar">{}<div class="bar">{}</div></body>'.format(BAR_STYLE, chips),
        getattr(sublime, "LAYOUT_BLOCK", 2),
        lambda key: _apply_plan(view) if key == "apply" else _dropdown(view, key))


def _send_text(view, text):
    """Envía un mensaje como si se hubiera escrito en el chat; False si no se puede ahora."""
    start = view.settings().get(K_START)
    if not view.is_valid() or start is None:
        return False
    view.run_command("claude_chat_replace", {"a": start, "b": view.size(), "text": text})
    view.run_command("claude_chat_send")
    return True


def _apply_plan(view):
    start = view.settings().get(K_START)
    if start is None:
        return
    # Lo que hubiera a medio escribir se suma como ajuste al plan.
    draft = view.substr(sublime.Region(start, view.size())).strip()
    _set_edit(view, True)
    _send_text(view, "Aplica el plan que propusiste.{}".format("\n" + draft if draft else ""))


def _refresh_bars(window):
    for view in _chats(window):
        _show_bar(view)


def _dropdown(view, key):
    """Despliega las opciones de un control justo donde está la barra."""
    window = view.window()
    found = [control for control in _controls(view) if control[0] == key]
    if window is None or not found:
        return
    _, _, options, current = found[0]
    rows = "".join(
        '<div class="item"><a href="{}:{}">{} {}</a> <span class="detail">{}</span></div>'.format(
            key, index, "●" if index == current else "○", html.escape(name),
            html.escape(detail))
        for index, (name, detail) in enumerate(options))

    def pick(href):
        view.hide_popup()
        index = int(href.rpartition(":")[2])
        if key == "mode":
            ClaudeChatModeCommand(window).choose(view, index)
        elif key == "model":
            ClaudeSelectModelCommand(window).choose(index)
        else:
            ClaudeSelectEffortCommand(window).choose(index)

    view.show_popup(
        '<body id="claude-dropdown">{}{}</body>'.format(BAR_STYLE, rows),
        location=_bar_point(view), max_width=640, max_height=480, on_navigate=pick)


def _end_turn(view, draft=""):
    """Deja la marca lista para que el usuario escriba su próximo mensaje."""
    if not view.is_valid():
        return
    _block(view)
    _write(view, MARK + draft)
    start = view.size() - len(draft)
    _transcripts[view.id()] = view.substr(sublime.Region(0, start))
    view.settings().set(K_START, start)
    _show_bar(view)
    _fold_controls(view)
    view.set_read_only(False)
    view.sel().clear()
    view.sel().add(sublime.Region(view.size(), view.size()))
    view.show(view.size())
    if hasattr(view, "clear_undo_stack"):
        # Deshacer no debe llevarse la conversación, sólo lo que se escribe.
        sublime.set_timeout(lambda: view.is_valid() and view.clear_undo_stack(), 0)


def _note(window, text, view=None):
    """Escribe un aviso suelto en el chat, fuera de una consulta."""
    view = view or _open_chat(window)
    draft = _begin_turn(view)
    _block(view)
    _write(view, text)
    _end_turn(view, draft)


def _rename(view):
    state = view.settings()
    number = state.get(K_NUMBER) or 1
    view.set_name("Claude{}{}".format(
        " {}".format(number) if number > 1 else "",
        " ✎" if state.get(K_EDIT) else " ◇" if state.get(K_PLAN) else ""))
    _show_bar(view)


def _set_edit(view, enabled):
    view.settings().set(K_EDIT, bool(enabled))
    if enabled:
        view.settings().erase(K_PLAN)
    _rename(view)


def _set_plan(view, enabled):
    view.settings().set(K_PLAN, bool(enabled))
    if enabled:
        view.settings().set(K_EDIT, False)
    _rename(view)


def _mode_label(view):
    state = view.settings()
    return "puede editar" if state.get(K_EDIT) else "modo plan" if state.get(K_PLAN) \
        else "sólo lectura"


def _system(note, tools):
    """Lo que se agrega al prompt de sistema: el modo, el método y lo que pida el usuario."""
    settings = _settings()
    parts = [note, core.method_note(tools) if settings.get("work_method", True) else "",
             str(settings.get("custom_instructions") or "").strip()]
    return "\n\n".join(part for part in parts if part) or None


def _read_tools():
    """Herramientas de los comandos que sólo leen: las de lectura, la web y los subagentes."""
    settings = _settings()
    return core.join_tools(
        settings.get("read_tools", core.READ_TOOLS),
        settings.get("web_tools", core.WEB_TOOLS),
        settings.get("agent_tools", core.AGENT_TOOLS))


def _chat_mode(view):
    """Con qué se lanza un mensaje del chat: (herramientas, modo de permisos, nota, sistema).

    La nota dice en qué modo está el chat; el sistema la lleva junto con el método.
    """
    settings, state = _settings(), view.settings()
    live = _live_enabled()
    edit, plan = bool(state.get(K_EDIT)), bool(state.get(K_PLAN))
    # Lo que necesita aprobación sólo se ofrece si hay quién la pida.
    asked = settings.get("ask_tools", "Bash") if edit \
        else settings.get("read_ask_tools", "Bash")
    tools = core.join_tools(
        settings.get("edit_tools", core.EDIT_TOOLS) if edit
        else settings.get("read_tools", core.READ_TOOLS),
        settings.get("web_tools", core.WEB_TOOLS),
        settings.get("agent_tools", core.AGENT_TOOLS),
        asked if live else "")
    note = core.chat_note(tools, edit, plan=plan and not edit, asks=live)
    mode = "acceptEdits" if edit else "plan" if plan else None
    return tools, mode, note, _system(note, tools)


# --- Contexto del editor ----------------------------------------------------

DIAGNOSTICS_LIMIT = 20
PERMISSION_LINE = 160  # largo de un comando que todavía se lee en un renglón


def _read_text(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, ValueError):
        return None


class _Context(object):
    """Lo que el editor sabe en el momento de la consulta."""

    def __init__(self, window):
        self.window = window
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
        # Sin selección, la línea del cursor dice de qué parte del archivo se habla.
        self.row = None
        if view and self.path and not self.dirty and self.region is None and len(view.sel()):
            self.row = view.rowcol(view.sel()[0].begin())[0] + 1
        self.diagnostics = []
        if view and self.path and _settings().get("context_diagnostics", True):
            # Sólo errores y advertencias, y pocos: es contexto, no la tarea.
            self.diagnostics = (_lsp_lines(view, self.rel, 2) or [])[:DIAGNOSTICS_LIMIT]

    def signature(self):
        """Cambia cuando cambia lo que describe(): evita repetirlo en cada mensaje."""
        view = self.view
        if view is None:
            return []
        region = [self.region.begin(), self.region.end()] if self.region else []
        changes = view.change_count() if self.dirty or not self.path else 0
        return [self.path or view.id(), region, changes, self.diagnostics, self.row]

    def describe(self):
        """Texto que acompaña al prompt: archivo, búfer sin guardar y selección."""
        view = self.view
        if view is None:
            return ""
        parts = []
        if self.path and not self.dirty:
            parts.append("Archivo abierto en el editor: `{}`{}.".format(
                self.rel, ", con el cursor en la línea {}".format(self.row) if self.row else ""))
        elif view.size():
            diff = None
            if self.path:
                diff = core.buffer_diff(
                    _read_text(self.path), view.substr(sublime.Region(0, view.size())), self.rel)
            if diff and len(diff) <= BUFFER_LIMIT:
                # Con el diff basta: el resto del archivo lo puede leer del disco.
                parts.append(
                    "Archivo abierto en el editor: `{}`. Tiene cambios sin guardar; este es el "
                    "diff del búfer contra el archivo en disco:\n<diff>\n{}\n</diff>".format(
                        self.rel, diff))
            elif self.path:
                title = "Archivo abierto en el editor: `{}`. Tiene cambios sin guardar".format(
                    self.rel)
            else:
                title = "Búfer sin guardar{}".format(
                    " ({})".format(self.syntax) if self.syntax else "")
            if not (diff and len(diff) <= BUFFER_LIMIT):
                text = view.substr(sublime.Region(0, min(view.size(), BUFFER_LIMIT)))
                note = " (recortado)" if view.size() > BUFFER_LIMIT else ""
                parts.append(
                    "{}; este es su contenido actual{}:\n<contenido>\n{}\n</contenido>".format(
                        title, note, text))
        if self.region is not None:
            first = view.rowcol(self.region.begin())[0] + 1
            last = view.rowcol(self.region.end())[0] + 1
            parts.append("Selección (líneas {}-{}):\n<seleccion>\n{}\n</seleccion>".format(
                first, last, view.substr(self.region)))
        if self.diagnostics:
            parts.append("Avisos del linter en ese archivo:\n{}".format(
                "\n".join(self.diagnostics)))
        tabs = self.tabs()
        if tabs:
            parts.append("Otras pestañas abiertas: {}.".format(
                ", ".join("`{}`".format(name) for name in tabs)))
        return "\n\n".join(parts)

    def tabs(self):
        """Los demás archivos del proyecto que están abiertos: suelen ser parte de la tarea."""
        prefix, names = self.cwd.rstrip(os.sep) + os.sep, []
        for view in self.window.views():
            path = view.file_name()
            if path and path != self.path and path.startswith(prefix) and not _is_chat(view):
                names.append(path[len(prefix):])
        return sorted(set(names))[:TABS_LIMIT]


def _with_context(prompt, context):
    described = context.describe()
    return "{}\n\n{}".format(prompt, described) if described else prompt


# --- Consultas --------------------------------------------------------------

def _ask(window, title, prompt, cwd, tools, mode=None, resume=None, echo=None,
         context=None, quiet=False, on_result=None, chat=None, system=None, note=None):
    """Lanza una consulta y escribe la respuesta en el chat.

    chat: el mensaje ya está escrito en ese chat (lo envió el usuario desde ahí).
    quiet: no toca el chat salvo que falle (se usa para generar código).
    note: la nota de modo que lleva `system`; el chat la recuerda para avisar si cambia.
    """
    target = chat if chat is not None else (None if quiet else _find_chat(window))
    if (quiet and _slot(window) in _runners) or (not quiet and _busy(target)):
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

    user_extra = list(settings.get("extra_args") or [])
    # Lo que no se pregunta (la web, de fábrica); sin herramientas no hay qué permitir.
    allowed = list(settings.get("allowed_tools", ["WebSearch", "WebFetch"]) or []) \
        if tools else []
    if system is None and tools and not quiet:
        # Un comando de sólo lectura: no tiene modo que explicar, pero sí método.
        system = _system("", tools)
    python = core.find_python()
    # Un hook copia cada archivo antes de que Claude lo toque: de ahí salen el
    # diff que se muestra al terminar y la posibilidad de revertir.
    review = bool(mode == "acceptEdits" and settings.get("review_edits", True) and python)
    edits_root = os.path.join(sublime.cache_path(), "ClaudeCode", "edits")
    started = time.time()
    status_view = window.active_view()
    result = {}
    turn = {"view": chat, "draft": ""}
    run = {}  # runner, sesión viva y respaldo de este intento

    def stamp():
        return str(int(time.time() * 1000))

    def shown():
        view = turn["view"]
        return not quiet and view is not None and view.is_valid()

    def current():
        return run.get("runner") is not None and _runners.get(run.get("key")) is run["runner"]

    def open_turn():
        view = turn["view"] = target or _open_chat(window)
        window.focus_view(view)
        turn["draft"] = _begin_turn(view)
        _block(view)
        _write(view, "## {}\n".format(title))
        if echo:
            _write(view, "> {}\n".format(echo.replace("\n", "\n> ")))

    def working(text):
        if status_view is not None and status_view.is_valid():
            status_view.set_status(STATUS_KEY, "Claude: {}".format(text))

    def say(line):
        """Una línea suelta en medio de la respuesta, sin pisar la de actividad."""
        if shown():
            _clear_activity(turn["view"], turn)
            _line(turn["view"])
            _write(turn["view"], line)

    def quoted(header, text):
        """Un bloque citado, plegado bajo su encabezado: se abre con su control."""
        if not shown() or not text.strip():
            return
        view = turn["view"]
        _clear_activity(view, turn)
        _line(view)
        start = view.size()
        _write(view, core.quote_block(header, text))
        _fold_quotes(view, start)

    def flush_thinking():
        text = turn.pop("thinking", "")
        if text and settings.get("show_thinking", True):
            quoted("  ✻ razonamiento", text)

    def show_output(data):
        """Lo que devolvió una herramienta, para no depender de que Claude lo cuente."""
        wanted = [name.strip() for name in str(settings.get("tool_output", "Bash") or "").split(",")]
        if data["tool"].split(" ", 1)[0] in wanted or "*" in wanted:
            label = data["tool"] if len(data["tool"]) <= 80 else data["tool"][:77] + "..."
            quoted("  ↳ {} · {}".format("falló" if data["error"] else "salida", label),
                   data["text"])

    def tick():
        """Cuenta los segundos de la espera: distingue «sigue pensando» de «se colgó»."""
        if not current():
            turn["ticking"] = False
            return
        label = turn.get("label")
        if label:
            text = "{} {} s".format(label, int(time.time() - turn["since"]))
            working(text)
            if shown():
                _show_activity(turn["view"], turn, text)
        sublime.set_timeout(tick, 1000)

    def ask_permission(data):
        """El CLI quiere usar algo que necesita permiso: decide quien está en el editor."""
        live = run.get("live")
        if live is None:
            return
        session, runner = live["session"], run.get("runner")
        command = data["input"].get("command") if data["tool"] == "Bash" else None
        command = str(command).strip() if command else ""
        what = "{} {}".format(data["tool"], " ".join(command.split())) if command \
            else data["summary"]
        turn.update(label="esperando tu permiso…", since=time.time())
        working(turn["label"])
        if "\n" in command or len(what) > PERMISSION_LINE:
            # Un guion entero en un renglón no se puede leer: va la primera línea y
            # el comando completo queda plegado debajo, para abrirlo antes de decidir.
            lines = command.splitlines()
            first = "{} {}".format(data["tool"], lines[0])
            what = first if len(first) <= PERMISSION_LINE else first[:PERMISSION_LINE - 3] + "..."
            say("  ? pide permiso: {} … ({} línea{}, completo aquí abajo)\n".format(
                what, len(lines), "" if len(lines) == 1 else "s"))
            if shown():
                view = turn["view"]
                start = view.size() + len("```bash")
                _write(view, "```bash\n{}\n```\n".format(command))
                script = sublime.Region(start, view.size() - len("\n```\n"))
                view.add_regions(SCRIPTS, list(view.get_regions(SCRIPTS)) + [script],
                                 flags=getattr(sublime, "HIDDEN", 32))
                view.fold(script)
                _fold_controls(view)
        else:
            say("  ? pide permiso: {}\n".format(what))
        if shown():
            _show_activity(turn["view"], turn, turn["label"])

        rule = core.permission_rule(data["tool"], data["input"])

        def decide(index):
            allow = index == 0 or (index == 1 and rule is not None)
            if allow and index == 1:
                session.allowed.add(rule)
            session.answer(data["id"], allow, data["input"])
            if run.get("runner") is not runner or not current():
                return
            turn.update(label="esperando la respuesta…", since=time.time())
            if not allow:
                say("  ✘ rechazado\n")

        items = [["Permitir", what]]
        if rule is not None:
            # Un comando compuesto o un intérprete no se ofrece: permitirlo siempre
            # sería permitir cualquier cosa.
            items.append(["Permitir siempre «{}» en esta conversación".format(
                core.rule_label(rule)), "No vuelve a preguntar por esto hasta cerrar el chat"])
        items.append(["Rechazar", data["description"] or "Claude sigue sin hacerlo"])
        window.show_quick_panel(items, decide, getattr(sublime, "KEEP_OPEN_ON_FOCUS_LOST", 0))

    def show_limit(info):
        text = core.limit_text(info)
        if not text:
            return
        window.status_message("Claude: {}".format(text))
        seen = "{}:{}".format(info.get("status"), info.get("rateLimitType"))
        if shown() and turn["view"].settings().get(K_LIMIT) != seen:
            # El aviso llega en cada consulta y el porcentaje va subiendo: en el chat
            # se anota una vez por conversación; la barra de estado lleva el dato al día.
            turn["view"].settings().set(K_LIMIT, seen)
            say("— {}\n".format(text))

    def handle(kind, data):
        view = turn["view"]
        if kind == "result":
            result.update(data)
            return
        if kind == "usage":
            turn["tokens"] = data
            return
        if kind == "session":
            if run.get("live") is not None:
                run["live"]["sid"] = data
            if shown():
                view.settings().set(K_SESSION, data)
            return
        if kind == "limit":
            show_limit(data)
            return
        if kind == "thinking":
            turn["thinking"] = turn.get("thinking", "") + data
            return
        if kind != "activity":
            flush_thinking()
        if kind == "output":
            show_output(data)
            return
        if kind == "permission":
            ask_permission(data)
            return
        turn["label"] = data if kind == "activity" else None
        turn["since"] = time.time()
        if kind in ("activity", "text_start"):
            working(data if kind == "activity" else "respondiendo…")
        if not shown():
            return
        _clear_activity(view, turn)
        if kind == "activity":
            _show_activity(view, turn, data)
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
        backup, live = run.get("backup"), run.get("live")
        if live is not None and live.get("backup"):
            # El hook de un proceso vivo escribe siempre en la misma carpeta: lo de
            # este turno se aparta para que el siguiente empiece limpio.
            if core.edited_files(live["backup"]):
                backup = core.rotate_backup(live["backup"], os.path.join(edits_root, stamp()))
            else:
                shutil.rmtree(live["backup"], ignore_errors=True)
                os.makedirs(live["backup"], exist_ok=True)
        if not backup:
            return ""
        report = core.edits_report(backup, cwd)
        changed = report["changed"]
        if not changed:
            shutil.rmtree(backup, ignore_errors=True)
            return ""
        core.mark_applied(backup)
        # Cada turno que edita deja su respaldo: revertir va deshaciendo de a uno.
        _set_backups(view, [path for path in _backups(view) if path != backup] + [backup])
        turn["changed"] = changed
        return ("### Cambios en {} archivo{}\n{}+{} −{} líneas{}. {}"
                "Para deshacerlos: «Claude: Revertir la última edición».\n").format(
            len(changed), "" if len(changed) == 1 else "s",
            "```diff\n{}\n```\n".format(report["diff"]), report["added"], report["removed"],
            " (de los archivos largos se muestra el comienzo)" if report["truncated"] else "",
            "Cada archivo viene plegado: «Claude: Plegar o desplegar los bloques del chat» "
            "(Ctrl+Alt+C, K) los abre. " if _settings().get("fold_edits", True) else "")

    def close_turn(text):
        view = turn["view"]
        if view is not None and view.is_valid():
            flush_thinking()
            _clear_activity(view, turn)
            if "from" in turn:
                _fold_code(view, turn["from"])
            summary = edits_summary(view)
            if summary:
                _block(view)
                start = view.size()
                _write(view, summary)
                _fold_diffs(view, start)
            _block(view)
            _write(view, text)
            _end_turn(view, turn["draft"])
            if not quiet:
                _save_history(view)
            files = turn.pop("changed", None)
            if files and settings.get("check_after_edit", True):
                # LSP tarda un momento en volver a mirar los archivos recién escritos.
                sublime.set_timeout(lambda: _check_edits(window, view, files, cwd), 2500)

    def keep_queued():
        """La respuesta no llegó a buen término: lo que estaba en cola vuelve al borrador."""
        view = turn["view"]
        waiting = _queued.pop(view.id(), []) if view is not None else []
        turn["draft"] = "\n".join(part for part in [turn["draft"]] + waiting if part)

    def finish(code, stderr, stopped):
        run["finished"] = True
        if current():
            _runners.pop(run["key"], None)
        if status_view is not None and status_view.is_valid():
            status_view.erase_status(STATUS_KEY)
        view, live = turn["view"], run.get("live")
        if live is not None and not live["session"].alive() and view is not None \
                and _sessions.get(view.id()) is live:
            _sessions.pop(view.id(), None)
            if not stopped and code != 0 and live["session"].events == 0:
                # Ni un evento: este CLI no acepta el modo persistente. Se repite la
                # consulta a la manera de siempre y no se vuelve a intentar.
                shutil.rmtree(live.get("backup") or "", ignore_errors=True)
                _live_off.append(stderr or "código {}".format(code))
                say("— el proceso persistente no arrancó; sigo con un proceso por mensaje\n")
                launch(False)
                return
        if stopped:
            keep_queued()
            close_turn("— detenido\n")
            window.status_message("Claude: detenido")
        elif code != 0 or result.get("is_error") or not result:
            if quiet:
                open_turn()
            keep_queued()
            detail = stderr or (result.get("result") if result.get("is_error") else "") or ""
            close_turn("**Error** (código {}){}\n".format(
                code, ": " + str(detail) if detail else ""))
        else:
            # El modo queda a la vista en cada turno: es el que rige el próximo mensaje.
            # Segundos cumplidos, como el contador de la espera: redondeado mostraba
            # «4 s» cuando en pantalla se había visto pasar hasta el 3.
            parts = ["listo ({} s)".format(int(time.time() - started))]
            if shown():
                parts.append(_mode_label(view))
            parts.append(core.context_text(turn.get("tokens"), result))
            if settings.get("effort"):
                parts.append("esfuerzo {}".format(settings.get("effort")))
            if settings.get("show_cost", False) and result.get("total_cost_usd") is not None:
                parts.append("US$ {:.2f}".format(result["total_cost_usd"]))
            close_turn("— {}\n".format(" · ".join(part for part in parts if part)))
            window.status_message("Claude: listo")
            if on_result:
                on_result(result.get("result") or "")
            if shown():
                _send_queued(view)
        if live is not None and not live["session"].alive() and live.get("backup"):
            shutil.rmtree(live["backup"], ignore_errors=True)

    def launch(use_live):
        """Arranca la consulta: en el proceso vivo del chat o en uno de un solo uso."""
        view = turn["view"]
        result.clear()
        run.update(live=None, backup=None, runner=None, finished=False)

        def on_event(kind, data):
            sublime.set_timeout(lambda: handle(kind, data), 0)

        def on_exit(code, err, stopped):
            sublime.set_timeout(lambda: finish(code, err, stopped), 0)

        def arguments(extra, backup, live):
            if backup:
                os.makedirs(backup, exist_ok=True)
                extra = extra + ["--settings", core.review_settings(python, HOOK, backup)]
            return core.build_args(
                claude, tools, permission_mode=mode, resume=resume,
                model=settings.get("model"), effort=settings.get("effort"),
                persist=not quiet, extra=extra, system=system, live=live,
                allowed=allowed, fallback=settings.get("fallback_model"))

        try:
            if use_live:
                signature = [claude, cwd, tools, mode, settings.get("model"),
                             settings.get("effort"), system, user_extra, review, allowed,
                             settings.get("fallback_model")]
                live = _sessions.get(view.id())
                if live is not None and not (
                        resume and live["key"] == signature and live.get("sid") == resume
                        and live["session"].alive()):
                    # Cambió el modo, el modelo o la conversación: ese proceso ya no sirve.
                    _drop_session(view.id())
                    live = None
                if live is None:
                    backup = os.path.join(
                        edits_root, "vivo-{}-{}".format(view.id(), stamp())) if review else None
                    session = core.Session(arguments(user_extra, backup, True), cwd)
                    session.start()
                    live = _sessions[view.id()] = {
                        "session": session, "key": signature, "backup": backup, "sid": None}
                run["live"] = live
                runner = live["session"].ask(prompt, on_event, on_exit)
            else:
                run["backup"] = os.path.join(edits_root, stamp()) if review else None
                runner = core.Runner(
                    arguments(user_extra, run["backup"], False), cwd, prompt, on_event, on_exit)
                runner.start()
        except OSError as error:
            sublime.error_message("No se pudo ejecutar Claude Code:\n\n{}".format(error))
            keep_queued()
            close_turn("**Error**: no se pudo ejecutar Claude Code.\n")
            return
        if run["finished"]:
            return
        run["runner"] = runner
        _runners[run["key"]] = runner
        turn.update(label="esperando la respuesta…", since=time.time())
        working(turn["label"])
        if shown():
            _show_activity(view, turn, turn["label"])
        if not turn.get("ticking"):
            # Al reintentar en el modo de reserva el contador ya está corriendo.
            turn["ticking"] = True
            sublime.set_timeout(tick, 1000)

    if not quiet:
        if chat is None:
            open_turn()
        state = turn["view"].settings()
        state.set(K_CWD, cwd)
        if not resume:
            state.erase(K_SESSION)
        if note:
            state.set(K_NOTE, note)
        else:
            state.erase(K_NOTE)
        if context is not None:
            state.set(K_CONTEXT, context.signature())
        turn["from"] = turn["view"].size()
    run["key"] = _slot(window, None if quiet else turn["view"])
    launch(not quiet and _live_enabled())


def _send_queued(view):
    """Envía el siguiente mensaje que quedó en cola mientras Claude respondía."""
    waiting = _queued.get(view.id())
    if not waiting or not view.is_valid() or view.settings().get(K_START) is None:
        return
    message = waiting.pop(0)
    if not waiting:
        _queued.pop(view.id(), None)
    draft = view.substr(sublime.Region(view.settings().get(K_START), view.size()))
    view.run_command("claude_chat_replace", {
        "a": view.size(), "b": view.size(), "text": ("\n" if draft.strip() else "") + message})
    view.run_command("claude_chat_send")


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
            # Enter mientras responde: el mensaje queda en cola en vez de perderse.
            ClaudeQueueMessageCommand(window).run()
            return
        message = view.substr(sublime.Region(start, view.size())).strip()
        if not message:
            return
        state.erase(K_START)
        view.erase_phantoms(BAR)
        view.set_read_only(True)

        context = _Context(window)
        session = state.get(K_SESSION)
        cwd = state.get(K_CWD) if session and state.get(K_CWD) else context.cwd
        tools, mode, note, system = _chat_mode(view)
        if SLASH.match(message):
            # Un comando de Claude Code (/compact, una skill) viaja tal cual: con algo
            # delante dejaría de ser un comando. El contexto y los avisos esperan.
            _ask(window, "", message, cwd, tools, mode=mode, resume=session,
                 chat=view, system=system, note=state.get(K_NOTE))
            return
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
        if session and state.get(K_NOTE) != note:
            # El prompt de sistema de una conversación ya empezada puede quedar como
            # nació: el modo vigente viaja con el mensaje para que no siga al anterior.
            prompt = "(Estado actual del chat, que reemplaza lo dicho antes sobre modo y " \
                     "herramientas: {})\n\n{}".format(note, prompt)
        _ask(window, "", prompt, cwd, tools, mode=mode, resume=session,
             context=context, chat=view, system=system, note=note)


class ClaudeCompactCommand(sublime_plugin.WindowCommand):
    """Resume la conversación para liberar contexto sin perder el hilo."""

    def run(self):
        chat = _find_chat(self.window)
        if chat is None or not chat.settings().get(K_SESSION):
            self.window.status_message("Claude: no hay una conversación que compactar.")
            return
        start = chat.settings().get(K_START)
        if start is None:
            self.window.status_message("Claude sigue trabajando; espera a que termine.")
            return
        if chat.substr(sublime.Region(start, chat.size())).strip():
            self.window.status_message(
                "Claude: envía o borra el mensaje a medio escribir antes de compactar.")
            return
        _send_text(chat, "/compact")


class ClaudeQueueMessageCommand(sublime_plugin.WindowCommand):
    """Deja un mensaje en cola: se envía solo cuando Claude termina de responder."""

    def run(self):
        chat = _find_chat(self.window)
        if not _busy(chat):
            # No hay nada que esperar: se escribe directamente en el chat.
            self.window.run_command("claude_chat")
            return
        self.window.status_message(
            "Claude sigue trabajando: lo que escribas queda en cola («Claude: Detener» lo corta).")
        self.window.show_input_panel(
            "Mensaje para cuando Claude termine:", "", lambda text: self.queue(chat, text),
            None, None)

    def queue(self, chat, text):
        text = text.strip()
        if not text or not chat.is_valid():
            return
        if not _busy(chat):
            # Terminó mientras se escribía: va al chat como un mensaje normal.
            _queued.setdefault(chat.id(), []).append(text)
            _send_queued(chat)
            return
        waiting = _queued.setdefault(chat.id(), [])
        waiting.append(text)
        self.window.status_message("Claude: {} mensaje{} en cola".format(
            len(waiting), "" if len(waiting) == 1 else "s"))


class ClaudeAnotherChatCommand(sublime_plugin.WindowCommand):
    """Abre un chat más: otra conversación, que puede correr mientras la primera responde."""

    def run(self):
        self.window.focus_view(_create_chat(self.window))


class ClaudeChatReplaceCommand(sublime_plugin.TextCommand):
    def run(self, edit, a, b, text):
        self.view.replace(edit, sublime.Region(a, b), text)


class ClaudeNewChatCommand(sublime_plugin.WindowCommand):
    """Vacía el chat y empieza una conversación nueva."""

    def run(self):
        view = _open_chat(self.window)
        if _busy(view):
            self.window.status_message("Claude sigue trabajando; detenlo antes de limpiar.")
            return
        _drop_session(view.id())
        _queued.pop(view.id(), None)
        state = view.settings()
        for key in (K_START, K_SESSION, K_CONTEXT, K_CWD, K_NOTICE, K_LIMIT, K_NOTE):
            state.erase(key)
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {"a": 0, "b": view.size(), "text": ""})
        _end_turn(view)


class ClaudeChatModeCommand(sublime_plugin.WindowCommand):
    """Elige entre sólo lectura, plan y edición para los próximos mensajes del chat."""

    MODES = (
        ("Sólo lectura", "Lee y responde; no modifica nada", "— el chat volvió a sólo lectura\n"),
        ("Plan", "Investiga y propone un plan, sin tocar archivos",
         "— el chat está en modo plan: propone y no modifica\n"),
        ("Edición", "Modifica archivos; lo demás lo aprueba quien está en el editor",
         "— el chat ahora puede editar archivos\n"),
    )

    def run(self):
        view = _open_chat(self.window, focus=False)
        state = view.settings()
        current = 2 if state.get(K_EDIT) else 1 if state.get(K_PLAN) else 0
        self.window.show_quick_panel(
            [["{}{}".format("● " if index == current else "", name), detail]
             for index, (name, detail, _) in enumerate(self.MODES)],
            lambda index: self.choose(view, index))

    def choose(self, view, index):
        if index < 0 or not view.is_valid():
            return
        state = view.settings()
        if index == (2 if state.get(K_EDIT) else 1 if state.get(K_PLAN) else 0):
            # Elegir el modo en que ya está no cambia nada: no se anota otra vez.
            return
        _set_edit(view, index == 2)
        _set_plan(view, index == 1)
        if view.settings().get(K_START) is not None:
            _note(self.window, self.MODES[index][2], view)
        self.window.status_message("Claude: chat en {}".format(_mode_label(view)))


class ClaudeToggleDiffsCommand(sublime_plugin.WindowCommand):
    """Despliega los diffs y el código largo del chat, o los vuelve a plegar."""

    def run(self):
        view = _find_chat(self.window)
        regions = _foldable(view) if view is not None else []
        if not regions:
            self.window.status_message("Claude: no hay bloques que plegar en el chat.")
            return
        folded = {(region.begin(), region.end()) for region in view.folded_regions()}
        if any((region.begin(), region.end()) in folded for region in regions):
            for region in regions:
                view.unfold(region)
        else:
            view.fold(regions)
        _fold_controls(view)


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
             _read_tools(), context=context)


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
             _read_tools())


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
             _read_tools())


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
    # Quien pide una edición espera poder seguir pidiéndolas desde el chat, así que
    # se lanza igual que un mensaje suyo: el proceso sirve para lo que venga después.
    chat = _open_chat(window, focus=False)
    _set_edit(chat, True)
    tools, mode, note, system = _chat_mode(chat)
    _ask(window, title, prompt, context.cwd, tools, mode=mode, echo=echo, context=context,
         system=system, note=note)


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
    # Severidad 4 son pistas de estilo (variables sin usar atenuadas): no van.
    return _lsp_lines(view, rel, 3)


def _lsp_lines(view, rel, severity):
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
            for item in session.diagnostics.get_diagnostics_for_uri(uri, severity):
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


def _check_edits(window, chat, files, cwd):
    """Los errores que LSP marca en lo recién editado viajan con el próximo mensaje."""
    if not chat.is_valid():
        return
    lines = []
    for path in files:
        view = window.find_open_file(path)
        if view is not None:
            lines += _lsp_lines(view, os.path.relpath(path, cwd), 1) or []
    if not lines:
        return
    lines = lines[:DIAGNOSTICS_LIMIT]
    state = chat.settings()
    previous = state.get(K_NOTICE)
    state.set(K_NOTICE, "{}Aviso: tras tu última edición el editor marca estos errores en "
                        "los archivos que tocaste:\n{}".format(
                            previous + " " if previous else "", "\n".join(lines)))
    if state.get(K_START) is not None:
        _note(window, "— el editor marca {} error{} en lo editado; Claude los verá con tu "
                      "próximo mensaje\n".format(len(lines), "" if len(lines) == 1 else "es"),
              chat)


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
             _read_tools())


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
        view = _open_chat(self.window)
        if _busy(view):
            self.window.status_message("Claude sigue trabajando; detenlo antes de cambiar de chat.")
            return
        entry = entries[index]
        _drop_session(view.id())
        _queued.pop(view.id(), None)
        state = view.settings()
        for key in (K_START, K_CONTEXT, K_NOTICE, K_LIMIT):
            state.erase(key)
        view.set_read_only(False)
        view.run_command("claude_chat_replace", {"a": 0, "b": view.size(), "text": entry["text"]})
        state.set(K_SESSION, entry["session"])
        state.set(K_CWD, entry.get("cwd"))
        _set_edit(view, entry.get("edit"))
        _set_plan(view, entry.get("plan") and not entry.get("edit"))
        _fold_diffs(view)
        _fold_code(view)
        _fold_quotes(view)
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
        _refresh_bars(self.window)
        self.window.status_message("Claude: modelo {}".format(name))


class ClaudeSelectEffortCommand(sublime_plugin.WindowCommand):
    """Elige cuánto razona Claude en las próximas consultas."""

    def run(self):
        current = _settings().get("effort") or ""
        items = [["{}{}".format("● " if effort == current else "", name), detail]
                 for effort, name, detail in EFFORTS]
        self.window.show_quick_panel(items, self.choose)

    def choose(self, index):
        if index < 0 or index >= len(EFFORTS):
            return
        effort, name, _ = EFFORTS[index]
        _settings().set("effort", effort)
        sublime.save_settings(SETTINGS)
        _refresh_bars(self.window)
        self.window.status_message("Claude: esfuerzo {}".format(name.lower()))


class ClaudeRevertEditCommand(sublime_plugin.WindowCommand):
    """Devuelve los archivos a como estaban antes de la última edición de Claude."""

    def run(self):
        chat = _find_chat(self.window)
        backup = chat.settings().get(K_BACKUP) if chat is not None else None
        if not backup or not os.path.isdir(backup):
            self.window.status_message("Claude: no hay una edición que revertir.")
            return
        if _busy(chat):
            self.window.status_message("Claude sigue trabajando; detenlo antes de revertir.")
            return
        files = core.edited_files(backup)
        if len(files) < 2:
            self.revert(chat, backup, None)
            return
        cwd = chat.settings().get(K_CWD) or ""
        items = [["Todos los archivos ({})".format(len(files)), "Deshace la edición completa"]]
        items += [[os.path.basename(path), os.path.relpath(path, cwd) if cwd else path]
                  for path in files]
        self.window.show_quick_panel(
            items, lambda index: index >= 0 and self.revert(
                chat, backup, None if index == 0 else [files[index - 1]]))

    def revert(self, chat, backup, only):
        changed = [path for path in core.changed_since(backup) if only is None or path in only]
        if changed and not sublime.ok_cancel_dialog(
                "Estos archivos cambiaron después de la edición de Claude:\n\n{}\n\n"
                "Si reviertes, esos cambios posteriores se pierden.".format(
                    "\n".join(os.path.basename(path) for path in changed)),
                "Revertir igual"):
            return
        try:
            restored = core.revert_edits(backup, only)
        except OSError as error:
            sublime.error_message("No se pudo revertir:\n\n{}".format(error))
            return
        names = ", ".join(os.path.basename(path) for path in restored) or "nada que cambiar"
        left = core.edited_files(backup)
        if not left:
            _set_backups(chat, [path for path in _backups(chat) if path != backup])
            shutil.rmtree(backup, ignore_errors=True)
        older = len(_backups(chat)) if not left else 0
        # Claude no ve el disco entre mensajes: hay que contárselo en el siguiente.
        previous = chat.settings().get(K_NOTICE)
        chat.settings().set(K_NOTICE, "{}Aviso: el usuario revirtió {} ({}).".format(
            previous + " " if previous else "",
            "parte de tu última edición" if left else "tu última edición", names))
        _note(self.window, "— edición revertida: {}{}{}\n".format(
            names, " (quedan {} sin revertir)".format(len(left)) if left else "",
            "; revertir otra vez deshace la edición anterior" if older else ""), chat)

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
        runner = _current_runner(self.window)
        if runner is not None:
            runner.stop()

    def is_enabled(self):
        return _current_runner(self.window) is not None


def _has_terminus():
    return any(name == "Terminus" or name.startswith("Terminus.") for name in sys.modules)


class ClaudeTerminalCommand(sublime_plugin.WindowCommand):
    """Abre Claude Code interactivo en una terminal, en la carpeta del proyecto."""

    def run(self, with_file=None):
        """with_file: True exige un archivo guardado, False no lo pasa; si no, va si lo hay."""
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
        if with_file and not context.rel:
            sublime.error_message("Guarda el archivo antes de abrirlo con Claude.")
            return
        if context.rel and with_file is not False:
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
        if window is None or view.element() is not None:
            return
        if _is_chat(view):
            _last_chat[window.id()] = view.id()
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
            _show_bar(view)
            _fold_controls(view)
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
        _queued.pop(self.view.id(), None)
        runner = _runners.get(_slot(None, self.view))
        if runner is not None:
            runner.stop()
        _drop_session(self.view.id())


def plugin_loaded():
    # Un chat restaurado al abrir Sublime pudo quedar a medio responder: se le
    # devuelve la marca para que se pueda seguir escribiendo.
    for window in sublime.windows():
        for view in _chats(window):
            if _busy(view):
                continue
            start = view.settings().get(K_START)
            intact = (start is not None and start <= view.size()
                      and view.substr(sublime.Region(start - len(MARK), start)) == MARK)
            if not intact:
                view.settings().erase(K_START)
                _end_turn(view)
            else:
                # Los controles no sobreviven al reinicio: se vuelven a poner.
                _show_bar(view)
                _fold_controls(view)


def plugin_unloaded():
    for runner in list(_runners.values()):
        runner.stop()
    for view_id in list(_sessions):
        _drop_session(view_id)

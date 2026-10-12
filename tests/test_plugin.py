"""El plugin contra un Sublime simulado: no llama al CLI ni necesita el editor."""
import importlib
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors = []


class Region(object):
    def __init__(self, a, b=None):
        self.a, self.b = a, a if b is None else b

    def begin(self):
        return min(self.a, self.b)

    def end(self):
        return max(self.a, self.b)

    def empty(self):
        return self.a == self.b


class Settings(dict):
    def set(self, key, value):
        self[key] = value

    def erase(self, key):
        self.pop(key, None)


class Selection(list):
    def clear(self):
        del self[:]

    def add(self, region):
        self.append(region)


class View(object):
    counter = 0

    def __init__(self, window, text="", path=None, dirty=False):
        View.counter += 1
        self._id, self._window = View.counter, window
        self.text, self.path, self.dirty = text, path, dirty
        self._sel, self._settings = Selection([Region(0)]), Settings()
        self.status, self.read_only, self.changes, self.name = {}, False, 0, ""
        self.valid, self.undo_cleared, self.undo_stack = True, 0, []
        self.folds = []

    def id(self): return self._id
    def window(self): return self._window
    def element(self): return None
    def file_name(self): return self.path
    def is_dirty(self): return self.dirty
    def is_valid(self): return self.valid
    def syntax(self): return types.SimpleNamespace(name="Python")
    def sel(self): return self._sel
    def size(self): return len(self.text)
    def substr(self, region): return self.text[max(0, region.begin()):region.end()]
    def rowcol(self, point): return (self.text.count("\n", 0, point), 0)
    def settings(self): return self._settings
    def assign_syntax(self, syntax): self.syntax_path = syntax
    def set_name(self, name): self.name = name
    def set_scratch(self, value): pass
    def set_read_only(self, value): self.read_only = value
    def is_read_only(self): return self.read_only
    def change_count(self): return self.changes
    def set_status(self, key, value): self.status[key] = value
    def erase_status(self, key): self.status.pop(key, None)
    def show(self, point): pass
    def folded_regions(self): return list(self.folds)

    def fold(self, regions):
        self.folds.extend(regions if isinstance(regions, list) else [regions])

    def unfold(self, region):
        self.folds = [fold for fold in self.folds
                      if (fold.begin(), fold.end()) != (region.begin(), region.end())]

    def visible(self):
        """El texto como se ve, con «…» en lugar de cada pliegue."""
        text = self.text
        for fold in sorted(self.folds, key=Region.begin, reverse=True):
            text = text[:fold.begin()] + "…" + text[fold.end():]
        return text
    def clear_undo_stack(self):
        self.undo_cleared += 1
        del self.undo_stack[:]

    def line(self, point):
        start = self.text.rfind("\n", 0, point) + 1
        end = self.text.find("\n", point)
        return Region(start, len(self.text) if end < 0 else end)

    def replace(self, edit, region, text):
        assert not self.read_only, "se intentó editar una vista de sólo lectura"
        self.text = self.text[:region.begin()] + text + self.text[region.end():]
        self.changes += 1

    def _modified(self):
        if plugin._is_chat(self):
            plugin.ClaudeChatListener(self).on_modified()

    def run_command(self, name, args=None):
        args = args or {}
        if name == "append":
            assert args.get("force") or not self.read_only
            self.text += args["characters"]
        elif name == "save":
            self.dirty = False
            return
        elif name == "undo":
            if self.undo_stack:
                self.text, selection = self.undo_stack.pop()
                self._sel[:] = selection
        else:
            cls = "".join(part.capitalize() for part in name.split("_")) + "Command"
            getattr(plugin, cls)(self).run(None, **args)
        self._modified()

    # Lo que haría una persona en el editor.
    def edit(self, begin, end, text):
        """Reemplaza un tramo como lo haría una tecla, con su paso de deshacer."""
        if self.read_only:
            return False
        self.undo_stack.append((self.text, list(self._sel)))
        self.text = self.text[:begin] + text + self.text[end:]
        self._sel[:] = [Region(begin + len(text))]
        self.changes += 1
        self._modified()
        return True

    def type(self, text):
        point = self._sel[0].begin()
        return self.edit(point, point, text)

    def click(self, point):
        self._sel[:] = [Region(point)]


class Window(object):
    def __init__(self, folders):
        self._folders, self._views, self.active = folders, [], None
        self.panels, self.messages, self.inputs = {}, [], []
        self.layout, self.groups, self.opened = None, {}, []
        self.quick, self.commands = [], []

    def id(self): return 1
    def folders(self): return self._folders
    def views(self): return list(self._views)
    def active_view(self): return self.active
    def num_groups(self): return 2 if self.layout else 1
    def set_layout(self, layout): self.layout = layout
    def get_view_index(self, view): return (self.groups.get(view.id(), 0), 0)
    def set_view_index(self, view, group, index): self.groups[view.id()] = group
    def views_in_group(self, group): return [v for v in self._views if self.groups.get(v.id(), 0) == group]
    def active_view_in_group(self, group): return next(iter(self.views_in_group(group)), None)
    def find_output_panel(self, name): return self.panels.get(name)
    def status_message(self, message): self.messages.append(message)
    def show_input_panel(self, caption, initial, on_done, a, b): self.inputs.append(on_done)
    def show_quick_panel(self, items, on_select, flags=0): self.quick.append((items, on_select))

    def run_command(self, name, args=None):
        self.commands.append((name, args))
    def open_file(self, name, flags=0, group=-1): self.opened.append((name, group))

    def add(self, view):
        self._views.append(view)
        self.focus_view(view)
        return view

    def new_file(self):
        return self.add(View(self))

    def focus_view(self, view):
        self.active = view
        plugin.ClaudeFileTracker().on_activated(view)


sublime = types.ModuleType("sublime")
sublime.Region = Region
sublime.ENCODED_POSITION = 1
timers = []  # lo programado con retardo no corre solo: la prueba decide cuándo
sublime.set_timeout = lambda function, delay=0: (
    timers.append(function) if delay else function())
sublime.error_message = errors.append
clipboard, dialogs = [""], {"answer": True, "shown": []}
sublime.get_clipboard = lambda: clipboard[0]
sublime.set_clipboard = lambda text: clipboard.__setitem__(0, text)
sublime.ok_cancel_dialog = lambda text, ok=None: (dialogs["shown"].append(text), dialogs["answer"])[1]
CACHE = tempfile.mkdtemp(prefix="claude-st-test-")
sublime.windows = lambda: []
sublime.cache_path = lambda: CACHE
sublime.packages_path = lambda: os.path.join(CACHE, "Packages")
saved_settings = []
sublime.save_settings = saved_settings.append
SETTINGS = Settings(
    claude_path="claude", read_tools="Read,Grep,Glob", edit_tools="Read,Edit,Write,Grep,Glob",
    extra_args=[], model="", effort="", chat_position="right", review_edits=True,
    remote_host="servidor", remote_log_lines=50,
    # Casi todas las pruebas describen el modo de un proceso por mensaje, que sigue
    # siendo el de reserva; las del proceso persistente lo encienden ellas.
    persistent_chat=False)
sublime.load_settings = lambda name: SETTINGS
sublime_plugin = types.ModuleType("sublime_plugin")


class _Command(object):
    def __init__(self, target=None):
        self.window = self.view = target


sublime_plugin.WindowCommand = sublime_plugin.TextCommand = _Command
sublime_plugin.EventListener = sublime_plugin.ViewEventListener = _Command
sys.modules.update(sublime=sublime, sublime_plugin=sublime_plugin)
# Dentro de Sublime el paquete se llama ClaudeCode, se llame como se llame la carpeta
# del proyecto (la instalada es un enlace a ésta).
package = types.ModuleType("ClaudeCode")
package.__path__ = [ROOT]
sys.modules["ClaudeCode"] = package
plugin = importlib.import_module("ClaudeCode.claude_plugin")

ANSWER = [("session", "s1"), ("text_start", None), ("text", "Miro el archivo."),
          ("tool", "Read src/a.py"), ("text_start", None),
          ("text", "`src/a.py:2` divide por cero."),
          ("result", {"result": "fin", "is_error": False})]


class FakeRunner(object):
    calls = []

    def __init__(self, args, cwd, prompt, on_event, on_exit):
        self.args, self.cwd, self.prompt = args, cwd, prompt
        self.on_event, self.on_exit = on_event, on_exit
        FakeRunner.calls.append(self)

    def start(self):
        pass

    def option(self, name):
        return self.args[self.args.index(name) + 1]

    def play(self, script=ANSWER, code=0, stderr="", stopped=False):
        if getattr(self, "session", None) is not None:
            self.session.events += len(script)
        for kind, data in script:
            self.on_event(kind, data)
        self.on_exit(code, stderr, stopped)

    def stop(self):
        self.play([], stopped=True)


class FakeSession(object):
    """El proceso persistente: cada mensaje es un FakeRunner más, con sus mismos argumentos."""
    made = []

    def __init__(self, args, cwd):
        self.args, self.cwd = args, cwd
        self.events, self.allowed, self.answers, self.dead = 0, set(), [], False
        FakeSession.made.append(self)

    def start(self):
        pass

    def alive(self):
        return not self.dead

    def close(self):
        self.dead = True

    def answer(self, request_id, allow, tool_input=None, message=""):
        self.answers.append((request_id, allow, tool_input))

    def ask(self, prompt, on_event, on_exit):
        turn = FakeRunner(self.args, self.cwd, prompt, on_event, on_exit)
        turn.session = self
        return turn


plugin.core.Runner = FakeRunner
plugin.core.Session = FakeSession
plugin.core.find_claude = lambda configured=None: "/bin/claude"
plugin._background = lambda function: function()


class ChatTest(unittest.TestCase):
    def setUp(self):
        FakeRunner.calls[:] = []
        plugin._runners.clear()
        plugin._sessions.clear()
        plugin._queued.clear()
        del plugin._live_off[:]
        plugin._last_file.clear()
        plugin._last_chat.clear()
        plugin._transcripts.clear()
        plugin._restoring.clear()
        plugin._testing.clear()
        del timers[:]
        __import__("shutil").rmtree(plugin._history_dir(), True)
        SETTINGS["model"] = ""
        del errors[:]
        clipboard[0] = ""
        dialogs.update(answer=True, shown=[])
        self.window = Window(["/p", "/p/app"])
        self.file = self.window.add(
            View(self.window, "def f():\n    return 1/0\n", "/p/app/src/a.py"))

    @property
    def chat(self):
        return plugin._find_chat(self.window)

    @property
    def last(self):
        return FakeRunner.calls[-1]

    def say(self, text):
        self.chat.click(self.chat.size())
        self.assertTrue(self.chat.type(text))
        self.chat.run_command("claude_chat_send")

    def test_revisar_y_responder_en_el_mismo_chat(self):
        plugin.ClaudeReviewFileCommand(self.window).run()
        chat = self.chat
        self.assertEqual((self.window.active, self.window.groups[chat.id()]), (chat, 1))
        self.assertEqual(self.window.layout["cols"], [0.0, 0.6, 1.0])
        self.assertTrue(chat.read_only)
        self.assertEqual(self.last.cwd, "/p/app")
        self.assertIn("el archivo `src/a.py`", self.last.prompt)
        self.assertFalse(chat.type("no debería entrar mientras responde"))
        self.last.play()
        self.assertTrue(chat.text.startswith(
            "## Revisión · src/a.py\n\nMiro el archivo.\n  ⚙ Read src/a.py\n\n"
            "`src/a.py:2` divide por cero.\n\n— listo ("))
        self.assertTrue(chat.text.endswith("\n\n❯ "))
        self.assertFalse(chat.read_only)
        self.assertEqual(chat.undo_cleared, 2)

        # La respuesta sigue la misma sesión; el archivo no cambió y no se repite.
        self.say("¿y cómo lo arreglo?")
        self.assertEqual(len(FakeRunner.calls), 2)
        self.assertEqual(self.last.option("--resume"), "s1")
        self.assertEqual(self.last.prompt, "¿y cómo lo arreglo?")
        self.assertEqual(self.last.option("--tools"), "Read,Grep,Glob")
        self.assertNotIn("--permission-mode", self.last.args)
        self.assertTrue(chat.read_only)
        self.last.play([("session", "s1"), ("text_start", None), ("text", "Con un try."),
                        ("result", {"result": "x", "is_error": False})])
        self.assertIn("\n\n❯ ¿y cómo lo arreglo?\n\nCon un try.\n\n— listo (", chat.text)
        self.assertTrue(chat.text.endswith("\n\n❯ "))
        self.assertEqual(chat.text.count("##"), 1)

    def test_chat_nuevo_lleva_el_contexto_del_archivo_de_trabajo(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.assertEqual(self.chat.text, "❯ ")
        self.say("¿qué hace este archivo?")
        self.assertNotIn("--resume", self.last.args)
        self.assertEqual(self.last.cwd, "/p/app")
        self.assertEqual(self.last.prompt,
                         "¿qué hace este archivo?\n\nArchivo abierto en el editor: `src/a.py`.")
        self.last.play()
        # Cambia la selección en el archivo: el contexto viaja de nuevo.
        self.file._sel[:] = [Region(13, 23)]
        self.say("¿y esto?")
        self.assertIn("Selección (líneas 2-2):\n<seleccion>\nreturn 1/0", self.last.prompt)
        self.last.play()
        self.say("gracias")
        self.assertEqual(self.last.prompt, "gracias")

    def test_se_puede_escribir_y_la_conversacion_se_protege_sola(self):
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.last.play()
        chat = self.chat
        base = chat.text
        size = chat.size()
        self.assertFalse(chat.read_only)
        # El cursor puede estar en cualquier parte sin bloquear la pestaña.
        chat.click(5)
        self.assertFalse(chat.read_only)
        self.assertTrue(chat.type("x"))          # escribir en la conversación…
        self.assertEqual(chat.text, base)        # …se deshace solo
        chat.click(size)
        self.assertTrue(chat.type("hola"))
        self.assertEqual(chat.text, base + "hola")
        chat.edit(size - 1, size, "")            # borrar hacia atrás sobre la marca
        self.assertEqual(chat.text, base + "hola")
        chat.edit(size - 2, chat.size(), "")     # borrar la línea entera
        self.assertEqual(chat.text, base + "hola")
        chat.edit(size + 1, size + 4, "")        # editar el propio mensaje sí se permite
        self.assertEqual(chat.text, base + "h")
        listener = plugin.ClaudeChatListener(chat)
        self.assertIsNone(listener.on_text_command("left_delete", None))
        chat.click(size)
        self.assertEqual(listener.on_text_command("left_delete", None), ("claude_noop", {}))
        self.assertEqual(listener.on_text_command("delete_word", {"forward": False}),
                         ("claude_noop", {}))
        self.assertIsNone(listener.on_text_command("delete_word", {"forward": True}))
        chat.click(chat.size())
        # Sin historial que deshacer, la conversación se reescribe igual.
        del chat.undo_stack[:]
        chat.text = chat.text[:10] + chat.text[40:]
        plugin.ClaudeChatListener(chat).on_modified()
        self.assertEqual(chat.text, base)
        self.assertEqual(chat.settings()[plugin.K_START], size)
        self.say("sigue funcionando")
        self.assertEqual(self.last.prompt, "sigue funcionando")

    def test_mensaje_vacio_y_envio_mientras_responde(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.chat.run_command("claude_chat_send")
        self.assertEqual(FakeRunner.calls, [])
        self.say("hola")
        self.chat.run_command("claude_chat_send")
        self.assertEqual(len(FakeRunner.calls), 1)
        self.assertIn("sigue trabajando", self.window.messages[-1])
        listener = plugin.ClaudeChatListener(self.chat)
        self.assertEqual(listener.on_text_command("undo", None), ("claude_noop", {}))
        self.last.play()
        self.assertIsNone(listener.on_text_command("undo", None))

    def test_un_comando_conserva_el_borrador_del_usuario(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.chat.click(self.chat.size())
        self.chat.type("a medio escribir")
        self.window.focus_view(self.file)
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.assertTrue(self.chat.text.startswith("## Revisión · src/a.py\n"))
        self.assertNotIn("a medio escribir", self.chat.text)
        self.last.play()
        self.assertTrue(self.chat.text.endswith("\n\n❯ a medio escribir"))
        self.assertEqual(self.chat.text[self.chat.settings()[plugin.K_START]:], "a medio escribir")

    def test_el_chat_muestra_que_hace_mientras_no_llega_texto(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("revisa esto")
        chat = self.chat
        self.assertTrue(chat.text.endswith("❯ revisa esto\n  ⋯ esperando la respuesta…\n"))
        self.assertEqual(self.file.status, {})
        self.assertEqual(chat.status[plugin.STATUS_KEY], "Claude: esperando la respuesta…")
        self.last.on_event("activity", "pensando…")
        self.assertTrue(chat.text.endswith("❯ revisa esto\n  ⋯ pensando…\n"))
        self.assertEqual(chat.status[plugin.STATUS_KEY], "Claude: pensando…")
        self.last.on_event("activity", "preparando Read…")
        self.assertEqual(chat.text.count("⋯"), 1)
        self.assertTrue(chat.read_only)
        self.last.play([("tool", "Read src/a.py"), ("activity", "esperando la respuesta…"),
                        ("text_start", None), ("text", "Listo."),
                        ("result", {"result": "x", "is_error": False})])
        # La línea de actividad no deja rastro en la conversación.
        self.assertNotIn("⋯", chat.text)
        self.assertIn("❯ revisa esto\n  ⚙ Read src/a.py\n\nListo.\n\n— listo (", chat.text)
        self.assertEqual(chat.status, {})

    def test_la_espera_cuenta_los_segundos(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("revisa esto")
        chat = self.chat
        self.assertEqual(len(timers), 1)
        self.last.on_event("activity", "pensando…")
        timers.pop()()
        self.assertTrue(chat.text.endswith("❯ revisa esto\n  ⋯ pensando… 0 s\n"))
        self.assertEqual(chat.status[plugin.STATUS_KEY], "Claude: pensando… 0 s")
        # Mientras escribe la respuesta no hay nada que contar.
        self.last.on_event("text_start", None)
        self.last.on_event("text", "Listo.")
        timers.pop()()
        self.assertTrue(chat.text.endswith("❯ revisa esto\n\nListo."))
        self.last.play([("result", {"result": "x", "is_error": False})])
        timers.pop()()
        self.assertEqual(timers, [])
        self.assertNotIn("⋯", chat.text)

    def test_sin_plegar_el_diff_queda_abierto(self):
        SETTINGS["fold_edits"] = False
        self.addCleanup(SETTINGS.pop, "fold_edits")
        folder, path = self.project()
        plugin.ClaudeEditFileCommand(self.window).run()
        self.window.inputs[-1]("devuelve cero")
        self.claude_edits(path, "def f():\n    return 0\n")
        self.last.play()
        self.assertEqual(self.chat.folds, [])
        self.assertNotIn("plegado", self.chat.text)
        plugin.ClaudeToggleDiffsCommand(self.window).run()
        self.assertNotIn("return 1/0", self.chat.visible())

    def test_detener_borra_la_linea_de_actividad(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("revisa esto")
        self.last.stop()
        self.assertNotIn("⋯", self.chat.text)
        self.assertIn("❯ revisa esto\n\n— detenido\n", self.chat.text)

    def test_claude_sabe_si_el_chat_puede_editar(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("arregla la división")
        note = self.last.option("--append-system-prompt")
        self.assertIn("sólo lectura", note)
        self.assertIn("Ctrl+Alt+C, M", note)
        self.last.play()
        self.assertRegex(self.chat.text, r"— listo \(\d+ s\) · sólo lectura\n")
        plugin.ClaudeToggleEditCommand(self.window).run()
        self.assertTrue(self.chat.text.endswith("— el chat ahora puede editar archivos\n\n❯ "))
        self.say("ahora sí")
        note = self.last.option("--append-system-prompt")
        self.assertIn("edición activada", note)
        self.assertNotIn("sólo lectura", note)
        self.last.play()
        self.assertRegex(self.chat.text, r"— listo \(\d+ s\) · puede editar\n")
        # Las consultas que no vienen del chat traen sus propias instrucciones.
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.assertNotIn("--append-system-prompt", self.last.args)

    def test_cambiar_el_modo_a_media_respuesta_no_parte_el_texto(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("revisa esto")
        before = self.chat.text
        plugin.ClaudeToggleEditCommand(self.window).run()
        self.assertEqual(self.chat.text, before)
        self.assertEqual(self.chat.name, "Claude ✎")

    def test_el_chat_puede_nacer_con_edicion(self):
        SETTINGS["chat_edit_default"] = True
        self.addCleanup(SETTINGS.pop, "chat_edit_default")
        plugin.ClaudeChatCommand(self.window).run()
        self.assertEqual((self.chat.name, self.chat.text), ("Claude ✎", "❯ "))
        self.say("arregla la división")
        self.assertEqual(self.last.option("--permission-mode"), "acceptEdits")

    # --- Proceso persistente, permisos y cola --------------------------------

    def live(self):
        SETTINGS["persistent_chat"] = True
        self.addCleanup(SETTINGS.__setitem__, "persistent_chat", False)
        FakeSession.made[:] = []

    def test_el_proceso_persistente_sirve_para_toda_la_conversacion(self):
        self.live()
        plugin.ClaudeChatCommand(self.window).run()
        self.say("hola")
        self.assertEqual(self.last.option("--input-format"), "stream-json")
        self.assertEqual(self.last.option("--permission-prompt-tool"), "stdio")
        self.last.play()
        self.say("¿y ahora?")
        self.assertEqual(len(FakeSession.made), 1)
        self.assertEqual(self.last.prompt, "¿y ahora?")
        self.assertNotIn("--resume", self.last.args)
        self.last.play([("session", "s1"), ("text_start", None), ("text", "Sigo."),
                        ("result", {"result": "x", "is_error": False})])
        self.assertIn("❯ ¿y ahora?\n\nSigo.\n\n— listo (", self.chat.text)

        # Otro modo son otras herramientas: proceso nuevo, que retoma la conversación.
        plugin.ClaudeToggleEditCommand(self.window).run()
        self.say("arréglalo")
        self.assertEqual(len(FakeSession.made), 2)
        self.assertTrue(FakeSession.made[0].dead)
        self.assertEqual(self.last.option("--resume"), "s1")
        self.assertEqual(self.last.option("--tools"), "Read,Edit,Write,Grep,Glob,Bash")
        self.assertIn("aprueba cada uno", self.last.option("--append-system-prompt"))
        self.last.play()

        plugin.ClaudeNewChatCommand(self.window).run()
        self.assertTrue(FakeSession.made[1].dead)
        self.assertEqual(plugin._sessions, {})
        self.say("otra conversación")
        self.assertEqual(len(FakeSession.made), 3)
        self.assertNotIn("--resume", self.last.args)

    def test_cerrar_el_chat_cierra_su_proceso(self):
        self.live()
        plugin.ClaudeChatCommand(self.window).run()
        self.say("hola")
        self.last.play()
        plugin.ClaudeChatListener(self.chat).on_pre_close()
        self.assertTrue(FakeSession.made[0].dead)
        self.assertEqual(plugin._sessions, {})

    def test_claude_pide_permiso_y_decide_quien_esta_en_el_editor(self):
        self.live()
        plugin.ClaudeChatCommand(self.window).run()
        plugin.ClaudeToggleEditCommand(self.window).run()
        self.say("corre las pruebas")
        chat, session = self.chat, FakeSession.made[0]

        def ask(request_id):
            data = {"id": request_id, "tool": "Bash", "input": {"command": "pytest   -q"},
                    "summary": "Bash pytest -q", "description": "", "blocked_path": ""}
            self.last.on_event("permission", data)
            return self.window.quick[-1]

        items, decide = ask("r1")
        self.assertEqual(items[0], ["Permitir", "Bash pytest -q"])
        self.assertIn("«Bash»", items[1][0])
        self.assertTrue(chat.text.endswith(
            "  ? pide permiso: Bash pytest -q\n  ⋯ esperando tu permiso…\n"))
        decide(0)
        self.assertEqual(session.answers, [("r1", True, {"command": "pytest   -q"})])
        self.assertNotIn("rechazado", chat.text)

        # Cerrar el panel sin elegir es no dar permiso.
        ask("r2")[1](-1)
        self.assertEqual(session.answers[-1][:2], ("r2", False))
        self.assertIn("  ✘ rechazado\n", chat.text)

        ask("r3")[1](1)
        self.assertEqual((session.answers[-1][:2], session.allowed), (("r3", True), {"Bash"}))

        # Un guion largo no se vuelca en un renglón: primera línea y el resto plegado.
        script = "cat > check.py <<'EOF'\nimport sys\nprint(sys.argv)\nEOF\npython3 check.py"
        self.last.on_event("permission", {
            "id": "r5", "tool": "Bash", "input": {"command": script},
            "summary": "Bash cat > check.py <<'EOF' import sys", "description": "",
            "blocked_path": ""})
        items, decide = self.window.quick[-1]
        self.assertEqual(items[0], ["Permitir", "Bash cat > check.py <<'EOF'"])
        self.assertIn("  ? pide permiso: Bash cat > check.py <<'EOF' … (5 líneas, completo aquí "
                      "abajo)\n```bash…\n```\n  ⋯ esperando tu permiso…\n", chat.visible())
        self.assertIn("```bash\n{}\n```\n".format(script), chat.text)
        decide(0)
        self.assertEqual(session.answers[-1], ("r5", True, {"command": script}))

        late = ask("r4")[1]
        self.last.play()
        after = chat.text
        late(2)  # la respuesta ya terminó: no se escribe nada fuera de lugar
        self.assertEqual(chat.text, after)
        self.assertNotIn("⋯", chat.text)

    def test_si_el_proceso_persistente_no_arranca_se_vuelve_al_modo_de_siempre(self):
        self.live()
        plugin.ClaudeChatCommand(self.window).run()
        self.say("hola")
        prompt = self.last.prompt
        FakeSession.made[0].dead = True
        self.last.on_exit(1, "error: unknown option '--input-format'", False)
        self.assertEqual(len(FakeRunner.calls), 2)
        self.assertNotIn("--input-format", self.last.args)
        self.assertEqual(self.last.prompt, prompt)
        self.assertIn("— el proceso persistente no arrancó", self.chat.text)
        self.last.play()
        self.assertIn("— listo (", self.chat.text)
        self.say("sigo")
        self.assertEqual(len(FakeSession.made), 1)
        self.assertEqual(self.last.option("--resume"), "s1")

    def test_un_proceso_que_muere_a_media_conversacion_se_repone(self):
        self.live()
        plugin.ClaudeChatCommand(self.window).run()
        self.say("hola")
        self.last.play()
        FakeSession.made[0].dead = True
        self.say("sigo")
        self.assertEqual(len(FakeSession.made), 2)
        self.assertEqual(self.last.option("--resume"), "s1")
        # Murió después de haber funcionado: es un error, no motivo para dejar el modo.
        FakeSession.made[1].events = 3
        FakeSession.made[1].dead = True
        self.last.on_exit(137, "", False)
        self.assertIn("**Error** (código 137)", self.chat.text)
        self.assertEqual(plugin._live_off, [])

    def test_una_edicion_por_turno_con_el_proceso_persistente(self):
        self.live()
        folder, path = self.project()
        plugin.ClaudeEditFileCommand(self.window).run()
        self.window.inputs[-1]("devuelve cero")
        self.claude_edits(path, "def f():\n    return 0\n")
        self.last.play()
        chat = self.chat
        self.assertIn("-    return 1/0\n+    return 0\n```\n", chat.text)
        first = chat.settings()[plugin.K_BACKUP]
        self.say("mejor que devuelva uno")
        self.assertEqual(len(FakeSession.made), 1)
        self.claude_edits(path, "def f():\n    return 1\n")
        self.last.play()
        # El diff es el de este turno, no el acumulado desde que arrancó el proceso.
        self.assertIn("-    return 0\n+    return 1\n```\n", chat.text)
        self.assertNotEqual(chat.settings()[plugin.K_BACKUP], first)
        plugin.ClaudeRevertEditCommand(self.window).run()
        with open(path) as handle:
            self.assertEqual(handle.read(), "def f():\n    return 0\n")

    def test_un_mensaje_en_cola_se_envia_al_terminar(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("uno")
        self.chat.run_command("claude_chat_send")   # Enter mientras responde
        self.window.inputs[-1]("dos")
        self.assertIn("1 mensaje en cola", self.window.messages[-1])
        self.assertEqual(len(FakeRunner.calls), 1)
        self.last.play()
        self.assertEqual(len(FakeRunner.calls), 2)
        self.assertEqual((self.last.prompt, self.last.option("--resume")), ("dos", "s1"))
        self.assertTrue(self.chat.text.endswith("\n\n❯ dos\n  ⋯ esperando la respuesta…\n"))
        self.assertEqual(plugin._queued, {})

    def test_al_detener_lo_que_estaba_en_cola_vuelve_al_borrador(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("uno")
        plugin.ClaudeQueueMessageCommand(self.window).run()
        self.window.inputs[-1]("dos")
        self.last.stop()
        self.assertEqual(len(FakeRunner.calls), 1)
        self.assertTrue(self.chat.text.endswith("— detenido\n\n❯ dos"))
        self.assertEqual(plugin._queued, {})

    # --- Modos, varios chats y cierre de turno -------------------------------

    def test_modo_plan(self):
        plugin.ClaudeChatCommand(self.window).run()
        plugin.ClaudeChatModeCommand(self.window).run()
        items, choose = self.window.quick[-1]
        self.assertEqual([item[0] for item in items], ["● Sólo lectura", "Plan", "Edición"])
        choose(1)
        self.assertEqual(self.chat.name, "Claude ◇")
        self.assertTrue(self.chat.text.endswith("— el chat está en modo plan: propone y no modifica\n\n❯ "))
        self.say("¿cómo lo harías?")
        self.assertEqual(self.last.option("--permission-mode"), "plan")
        self.assertEqual(self.last.option("--tools"), "Read,Grep,Glob")
        self.assertIn("modo plan", self.last.option("--append-system-prompt"))
        self.last.play()
        self.assertRegex(self.chat.text, r"— listo \(\d+ s\) · modo plan\n")
        plugin.ClaudeToggleEditCommand(self.window).run()
        self.assertEqual(self.chat.name, "Claude ✎")
        self.say("adelante")
        self.assertEqual(self.last.option("--permission-mode"), "acceptEdits")

    def test_dos_chats_trabajan_a_la_vez(self):
        plugin.ClaudeChatCommand(self.window).run()
        first = self.chat
        self.say("uno")
        plugin.ClaudeAnotherChatCommand(self.window).run()
        second = self.chat
        self.assertIsNot(first, second)
        self.assertEqual((first.name, second.name), ("Claude", "Claude 2"))
        self.say("dos")
        self.assertEqual((len(FakeRunner.calls), len(plugin._runners)), (2, 2))
        self.assertNotIn("--resume", self.last.args)
        # «Detener» corta el chat en uso; el otro sigue.
        plugin.ClaudeStopCommand(self.window).run()
        self.assertTrue(second.text.endswith("— detenido\n\n❯ "))
        self.assertTrue(first.read_only)
        FakeRunner.calls[0].play()
        self.assertIn("— listo (", first.text)
        self.assertEqual(plugin._runners, {})
        self.assertNotIn("Miro el archivo", second.text)
        # Los comandos responden en el último chat que se usó.
        self.window.focus_view(self.file)
        self.assertIs(self.chat, second)
        self.window.focus_view(first)
        self.window.focus_view(self.file)
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.assertIn("## Revisión · src/a.py", first.text)
        self.assertNotIn("## Revisión", second.text)

    def test_revertir_un_solo_archivo_de_la_edicion(self):
        folder, path = self.project()
        other = os.path.join(folder, "otro.py")
        with open(other, "w") as handle:
            handle.write("x = 1\n")
        plugin.ClaudeEditFileCommand(self.window).run()
        self.window.inputs[-1]("cambia los dos")
        self.claude_edits(path, "def f():\n    return 0\n")
        self.claude_edits(other, "x = 2\n")
        self.last.play()
        chat = self.chat
        self.assertIn("### Cambios en 2 archivos\n", chat.text)
        plugin.ClaudeRevertEditCommand(self.window).run()
        items, choose = self.window.quick[-1]
        self.assertEqual([item[0] for item in items],
                         ["Todos los archivos (2)", "calc.py", "otro.py"])
        choose(2)
        with open(path) as one, open(other) as two:
            self.assertEqual((one.read(), two.read()), ("def f():\n    return 0\n", "x = 1\n"))
        self.assertTrue(chat.text.endswith(
            "— edición revertida: otro.py (quedan 1 sin revertir)\n\n❯ "))
        self.assertIn("parte de tu última edición (otro.py)", chat.settings()[plugin.K_NOTICE])
        self.assertTrue(plugin.ClaudeRevertEditCommand(self.window).is_enabled())
        # Queda uno: se revierte sin preguntar cuál.
        plugin.ClaudeRevertEditCommand(self.window).run()
        with open(path) as handle:
            self.assertEqual(handle.read(), "def f():\n    return 1/0\n")
        self.assertFalse(plugin.ClaudeRevertEditCommand(self.window).is_enabled())

    def test_el_cierre_dice_cuanto_contexto_va_y_avisa_el_limite_una_vez(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("hola")
        info = {"status": "rejected", "rateLimitType": "five_hour"}
        warning = {"status": "allowed_warning", "rateLimitType": "seven_day", "utilization": 0.52}
        self.last.play([
            ("session", "s1"), ("limit", info), ("limit", info), ("limit", warning),
            ("limit", dict(warning, utilization=0.53)), ("text_start", None),
            ("text", "Hola."), ("usage", 76000),
            ("result", {"result": "x", "is_error": False, "total_cost_usd": 0.5,
                        "modelUsage": {"m": {"contextWindow": 200000}}})])
        chat = self.chat
        self.assertEqual(chat.text.count("— límite de uso de 5 horas alcanzado\n"), 1)
        # El porcentaje sube durante la conversación: se anota la primera vez.
        self.assertEqual(chat.text.count("— límite de uso semanal"), 1)
        self.assertIn("— límite de uso semanal al 52 %\n", chat.text)
        self.assertIn("al 53 %", self.window.messages[-2])
        self.assertRegex(chat.text, r"— listo \(\d+ s\) · sólo lectura · contexto 38 %\n")
        self.assertNotIn("US$", chat.text)
        SETTINGS["show_cost"] = True
        self.addCleanup(SETTINGS.pop, "show_cost")
        self.say("otra")
        self.last.play([("session", "s1"), ("text_start", None), ("text", "Vale."),
                        ("result", {"result": "x", "is_error": False, "total_cost_usd": 0.5})])
        self.assertRegex(chat.text, r"— listo \(\d+ s\) · sólo lectura · US\$ 0\.50\n")

    def test_el_codigo_largo_de_una_respuesta_llega_plegado(self):
        plugin.ClaudeChatCommand(self.window).run()
        self.say("muéstrame el módulo")
        code = "\n".join("linea {}".format(n) for n in range(60))
        self.last.play([("session", "s1"), ("text_start", None),
                        ("text", "Aquí va:\n```python\n{}\n```\nY uno corto:\n```\nx = 1\n```\n".format(code)),
                        ("result", {"result": "x", "is_error": False})])
        chat = self.chat
        self.assertIn("linea 3…\n```\nY uno corto:\n```\nx = 1\n```", chat.visible())
        self.assertNotIn("linea 30", chat.visible())
        plugin.ClaudeToggleDiffsCommand(self.window).run()
        self.assertEqual(chat.visible(), chat.text)
        SETTINGS["fold_code_lines"] = 0
        self.addCleanup(SETTINGS.pop, "fold_code_lines")
        plugin.ClaudeToggleDiffsCommand(self.window).run()
        self.assertIn("no hay bloques", self.window.messages[-1])

    def test_un_bufer_con_cambios_sin_guardar_viaja_como_diff(self):
        folder, path = self.project()
        disk = "".join("linea {}\n".format(n) for n in range(200))
        with open(path, "w") as handle:
            handle.write(disk)
        self.file.text, self.file.dirty = disk.replace("linea 100\n", "linea cien\n"), True
        plugin.ClaudeChatCommand(self.window).run()
        self.say("¿qué cambié?")
        self.assertIn("diff del búfer contra el archivo en disco:\n<diff>\n--- a/calc.py",
                      self.last.prompt)
        self.assertIn("-linea 100\n+linea cien", self.last.prompt)
        self.assertNotIn("<contenido>", self.last.prompt)
        self.assertNotIn("linea 5\n", self.last.prompt)

    def test_los_avisos_del_linter_viajan_con_el_archivo(self):
        original = plugin._lsp_lines
        self.addCleanup(setattr, plugin, "_lsp_lines", original)
        found = ["src/a.py:2:12: [pyright] división por cero"]
        plugin._lsp_lines = lambda view, rel, severity: list(found)
        plugin.ClaudeChatCommand(self.window).run()
        self.say("revisa")
        self.assertIn("Avisos del linter en ese archivo:\nsrc/a.py:2:12: [pyright] división",
                      self.last.prompt)
        self.last.play()
        self.say("¿algo más?")     # nada cambió: el contexto no se repite
        self.assertEqual(self.last.prompt, "¿algo más?")
        self.last.play()
        found.append("src/a.py:1:1: [ruff] falta docstring")
        self.say("¿y ahora?")      # cambiaron los avisos: vuelve a viajar
        self.assertIn("falta docstring", self.last.prompt)
        SETTINGS["context_diagnostics"] = False
        self.addCleanup(SETTINGS.pop, "context_diagnostics")
        self.last.play()
        plugin.ClaudeNewChatCommand(self.window).run()
        self.say("hola")
        self.assertNotIn("Avisos del linter", self.last.prompt)

    def test_editar_deja_el_chat_con_permiso_de_edicion(self):
        self.file.dirty = True
        plugin.ClaudeEditFileCommand(self.window).run()
        self.window.inputs[-1]("maneja la división por cero")
        self.assertFalse(self.file.dirty)
        self.assertEqual(self.last.option("--permission-mode"), "acceptEdits")
        self.assertEqual(self.last.option("--tools"), "Read,Edit,Write,Grep,Glob")
        self.assertIn("## Edición · src/a.py\n> maneja la división por cero\n", self.chat.text)
        self.assertEqual(self.chat.name, "Claude ✎")
        self.last.play()
        self.say("ahora agrega una prueba")
        self.assertEqual(self.last.option("--permission-mode"), "acceptEdits")
        self.assertEqual(self.last.option("--resume"), "s1")
        self.last.play()
        plugin.ClaudeToggleEditCommand(self.window).run()
        self.assertEqual(self.chat.name, "Claude")
        self.say("otra cosa")
        self.assertNotIn("--permission-mode", self.last.args)

    def test_escribir_codigo_no_toca_el_chat(self):
        self.file._sel[:] = [Region(13, 23)]
        plugin.ClaudeWriteCodeCommand(self.window).run()
        self.window.inputs[-1]("devuelve cero")
        self.assertEqual(self.last.option("--tools"), "")
        self.assertIn("--no-session-persistence", self.last.args)
        self.assertIn("<seleccion_a_reemplazar>\nreturn 1/0</seleccion_a_reemplazar>",
                      self.last.prompt)
        self.last.play([("session", "s9"), ("text", "ruido"),
                        ("result", {"result": "```python\nreturn 0\n```", "is_error": False})])
        self.assertEqual(self.file.text, "def f():\n    return 0\n")
        self.assertIsNone(self.chat)

        self.file._sel[:] = [Region(0)]
        plugin.ClaudeWriteCodeCommand(self.window).run()
        self.window.inputs[-1]("un import")
        self.file.changes += 1
        self.last.play([("result", {"result": "import os", "is_error": False})])
        self.assertEqual(self.file.text, "def f():\n    return 0\n")
        self.assertIn("no se insertó:\n\n```\nimport os\n```\n\n❯ ", self.chat.text)

    def test_error_detener_y_chat_nuevo(self):
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.last.play([], code=1, stderr="Not logged in")
        self.assertTrue(self.chat.text.endswith("**Error** (código 1): Not logged in\n\n❯ "))
        self.assertFalse(self.chat.read_only)
        self.say("hola")
        self.assertTrue(plugin.ClaudeStopCommand(self.window).is_enabled())
        plugin.ClaudeNewChatCommand(self.window).run()
        self.assertIn("detenlo antes", self.window.messages[-1])
        plugin.ClaudeStopCommand(self.window).run()
        self.assertTrue(self.chat.text.endswith("❯ hola\n\n— detenido\n\n❯ "))
        self.assertFalse(plugin.ClaudeStopCommand(self.window).is_enabled())
        self.chat.settings().set(plugin.K_SESSION, "s1")
        plugin.ClaudeNewChatCommand(self.window).run()
        self.assertEqual(self.chat.text, "❯ ")
        self.assertNotIn(plugin.K_SESSION, self.chat.settings())
        self.say("empiezo de nuevo")
        self.assertNotIn("--resume", self.last.args)

    def test_doble_clic_en_una_cita_abre_el_archivo(self):
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "src"))
            path = os.path.join(folder, "src", "a.py")
            open(path, "w").close()
            plugin.ClaudeChatCommand(self.window).run()
            chat = self.chat
            chat.settings().set(plugin.K_CWD, folder)
            chat.text = "Mira `src/a.py:2` y `no/existe.py:9`.\n\n❯ "
            listener = plugin.ClaudeChatListener(chat)
            chat._sel[:] = [Region(chat.text.index("a.py"))]
            listener.on_post_text_command("drag_select", {"by": "words"})
            self.assertEqual(self.window.opened, [(path + ":2", 0)])
            chat._sel[:] = [Region(chat.text.index("existe"))]
            listener.on_post_text_command("drag_select", {"by": "words"})
            chat._sel[:] = [Region(1)]
            listener.on_post_text_command("drag_select", {"by": "words"})
            listener.on_post_text_command("drag_select", {})
            self.assertEqual(len(self.window.opened), 1)

    def test_chat_restaurado_tras_reiniciar_recupera_la_marca(self):
        plugin.ClaudeReviewFileCommand(self.window).run()   # quedó a medio responder
        chat = self.chat
        plugin._runners.clear()
        sublime.windows = lambda: [self.window]
        try:
            plugin.plugin_loaded()
            self.assertTrue(chat.text.endswith("\n\n❯ "))
            self.assertFalse(chat.read_only)
            before = chat.text
            plugin.plugin_loaded()
            self.assertEqual(chat.text, before)
        finally:
            sublime.windows = lambda: []

    def test_cerrar_el_chat_detiene_la_consulta(self):
        plugin.ClaudeReviewFileCommand(self.window).run()
        chat = self.chat
        chat.valid = False
        plugin.ClaudeChatListener(chat).on_pre_close()
        self.assertEqual(plugin._runners, {})

    def project(self):
        """Un proyecto real en disco con un archivo abierto en el editor."""
        folder = tempfile.mkdtemp(prefix="claude-st-proj-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        path = os.path.join(folder, "calc.py")
        with open(path, "w") as handle:
            handle.write("def f():\n    return 1/0\n")
        self.window._folders = [folder]
        self.file = self.window.add(View(self.window, "def f():\n    return 1/0\n", path))
        return folder, path

    def claude_edits(self, path, text):
        """Hace lo que haría Claude: el hook respalda y luego se escribe el archivo."""
        import json as _json
        import subprocess
        settings = _json.loads(self.last.option("--settings"))
        command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        event = _json.dumps({"tool_name": "Edit", "cwd": self.last.cwd,
                             "tool_input": {"file_path": path}})
        subprocess.run(command, shell=True, input=event.encode(), check=True)
        with open(path, "w") as handle:
            handle.write(text)

    def test_una_edicion_muestra_su_diff_y_se_puede_revertir(self):
        folder, path = self.project()
        plugin.ClaudeEditFileCommand(self.window).run()
        self.window.inputs[-1]("devuelve cero")
        self.assertFalse(plugin.ClaudeRevertEditCommand(self.window).is_enabled())
        self.claude_edits(path, "def f():\n    return 0\n")
        self.last.play()
        chat = self.chat
        self.assertIn("### Cambios en 1 archivo\n```diff\n--- a/calc.py\n+++ b/calc.py\n", chat.text)
        self.assertIn("-    return 1/0\n+    return 0\n```\n+1 −1 líneas. ", chat.text)
        # Llega plegado: del archivo se ve el encabezado, no el código.
        self.assertIn("```diff\n--- a/calc.py\n+++ b/calc.py…\n```\n", chat.visible())
        plugin.ClaudeToggleDiffsCommand(self.window).run()
        self.assertEqual(chat.visible(), chat.text)
        plugin.ClaudeToggleDiffsCommand(self.window).run()
        self.assertNotIn("return 1/0", chat.visible())
        self.assertLess(chat.text.index("### Cambios"), chat.text.index("— listo"))
        self.assertTrue(plugin.ClaudeRevertEditCommand(self.window).is_enabled())

        plugin.ClaudeRevertEditCommand(self.window).run()
        with open(path) as handle:
            self.assertEqual(handle.read(), "def f():\n    return 1/0\n")
        self.assertTrue(chat.text.endswith("— edición revertida: calc.py\n\n❯ "))
        self.assertEqual(dialogs["shown"], [])
        self.assertFalse(plugin.ClaudeRevertEditCommand(self.window).is_enabled())
        # Claude se entera de la reversión en el mensaje siguiente, y sólo en ese.
        self.say("mejor de otra forma")
        self.assertTrue(self.last.prompt.startswith(
            "(Aviso: el usuario revirtió tu última edición (calc.py).)\n\nmejor de otra forma"))
        self.last.play()
        self.say("gracias")
        self.assertEqual(self.last.prompt, "gracias")

    def test_revertir_avisa_si_el_archivo_cambio_despues(self):
        folder, path = self.project()
        plugin.ClaudeEditFileCommand(self.window).run()
        self.window.inputs[-1]("devuelve cero")
        self.claude_edits(path, "def f():\n    return 0\n")
        self.last.play()
        with open(path, "a") as handle:
            handle.write("# seguí a mano\n")
        dialogs["answer"] = False
        plugin.ClaudeRevertEditCommand(self.window).run()
        self.assertIn("calc.py", dialogs["shown"][0])
        with open(path) as handle:
            self.assertIn("# seguí a mano", handle.read())
        dialogs["answer"] = True
        plugin.ClaudeRevertEditCommand(self.window).run()
        with open(path) as handle:
            self.assertEqual(handle.read(), "def f():\n    return 1/0\n")

    def test_sin_cambios_no_hay_diff_ni_respaldo_en_lectura(self):
        folder, path = self.project()
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.assertNotIn("--settings", self.last.args)
        self.last.play()
        plugin.ClaudeEditFileCommand(self.window).run()
        self.window.inputs[-1]("nada")
        self.last.play()
        self.assertNotIn("### Cambios", self.chat.text)
        self.assertFalse(plugin.ClaudeRevertEditCommand(self.window).is_enabled())
        SETTINGS["review_edits"] = False
        try:
            plugin.ClaudeEditFileCommand(self.window).run()
            self.window.inputs[-1]("nada")
            self.assertNotIn("--settings", self.last.args)
        finally:
            SETTINGS["review_edits"] = True

    def test_corregir_avisos_usa_lsp_y_si_no_ruff(self):
        folder, path = self.project()
        seen = {}
        original = (plugin._lsp_diagnostics, plugin.core.find_ruff, plugin.core.ruff_diagnostics)
        self.addCleanup(lambda: (setattr(plugin, "_lsp_diagnostics", original[0]),
                                 setattr(plugin.core, "find_ruff", original[1]),
                                 setattr(plugin.core, "ruff_diagnostics", original[2])))
        plugin._lsp_diagnostics = lambda view, rel: ["calc.py:2:12: [Pyright] División por cero"]
        plugin.core.find_ruff = lambda cwd, configured=None: seen.setdefault("ruff", "/bin/ruff")
        plugin.core.ruff_diagnostics = lambda ruff, file, cwd: ["calc.py:1:1: F401 sin usar"]
        plugin.ClaudeFixDiagnosticsCommand(self.window).run()
        self.assertIn("calc.py:2:12: [Pyright] División por cero", self.last.prompt)
        self.assertNotIn("F401", self.last.prompt)
        self.assertEqual(seen, {})
        self.assertEqual(self.last.option("--permission-mode"), "acceptEdits")
        self.assertIn("## Avisos del linter · calc.py\n> 1 aviso de LSP\n", self.chat.text)
        self.last.play()

        plugin._lsp_diagnostics = lambda view, rel: None      # LSP no atiende el archivo
        plugin.ClaudeFixDiagnosticsCommand(self.window).run()
        self.assertIn("calc.py:1:1: F401 sin usar", self.last.prompt)
        self.assertIn("> 1 aviso de ruff\n", self.chat.text)
        self.last.play()

        calls = len(FakeRunner.calls)
        plugin.core.ruff_diagnostics = lambda ruff, file, cwd: []
        plugin.ClaudeFixDiagnosticsCommand(self.window).run()
        self.assertEqual(len(FakeRunner.calls), calls)
        self.assertIn("no marca nada", self.window.messages[-1])
        plugin.core.find_ruff = lambda cwd, configured=None: None
        plugin.ClaudeFixDiagnosticsCommand(self.window).run()
        self.assertIn("no se encontró ruff", errors[-1])
        plugin._lsp_diagnostics = lambda view, rel: []        # LSP atiende y no marca nada
        plugin.ClaudeFixDiagnosticsCommand(self.window).run()
        self.assertEqual(len(FakeRunner.calls), calls)
        self.assertEqual(len(errors), 1)

    def test_generar_pruebas(self):
        folder, path = self.project()
        plugin.ClaudeWriteTestsCommand(self.window).run()
        self.assertIn("pruebas con pytest para el módulo de `calc.py`", self.last.prompt)
        self.assertEqual(self.last.option("--tools"), "Read,Edit,Write,Grep,Glob")
        self.assertEqual(self.chat.name, "Claude ✎")
        self.last.play()
        self.file._sel[:] = [Region(0, 8)]
        plugin.ClaudeWriteTestsCommand(self.window).run()
        self.assertIn("para el código seleccionado de `calc.py`", self.last.prompt)
        self.assertIn("<seleccion>\ndef f():", self.last.prompt)

    def test_mensaje_de_commit_va_al_chat_y_al_portapapeles(self):
        original = plugin.core.commit_context
        self.addCleanup(setattr, plugin.core, "commit_context", original)
        plugin.core.commit_context = lambda cwd: ("", False, "")
        plugin.ClaudeCommitMessageCommand(self.window).run()
        self.assertEqual(FakeRunner.calls, [])
        plugin.core.commit_context = lambda cwd: ("diff --git a/x b/x\n+1\n", True, "feat: algo\n")
        plugin.ClaudeCommitMessageCommand(self.window).run()
        self.assertEqual(self.last.option("--tools"), "")
        self.assertIn("<ultimos_commits>\nfeat: algo\n</ultimos_commits>", self.last.prompt)
        self.assertIn("<diff>\ndiff --git a/x b/x", self.last.prompt)
        self.last.play([("result", {"result": "```\nfix: evita dividir por cero\n```", "is_error": False})])
        self.assertEqual(clipboard[0], "fix: evita dividir por cero")
        self.assertIn("## Mensaje de commit\nDescribe lo preparado con `git add`; quedó copiado",
                      self.chat.text)
        self.assertTrue(self.chat.text.endswith("```\nfix: evita dividir por cero\n```\n\n❯ "))

    def test_explicar_el_error_del_portapapeles(self):
        plugin.ClaudeExplainClipboardCommand(self.window).run()
        self.assertEqual(FakeRunner.calls, [])
        self.assertIn("portapapeles está vacío", self.window.messages[-1])
        clipboard[0] = "Traceback (most recent call last):\n  File \"a.py\", line 2\nZeroDivisionError: division by zero\n"
        plugin.ClaudeExplainClipboardCommand(self.window).run()
        self.assertIn("<error>\nTraceback (most recent call last):", self.last.prompt)
        self.assertIn("## Error del portapapeles\n> ZeroDivisionError: division by zero\n",
                      self.chat.text)
        self.assertEqual(self.last.option("--tools"), "Read,Grep,Glob")

    def test_logs_del_servidor(self):
        commands = []
        original = plugin.core.ssh_run
        self.addCleanup(setattr, plugin.core, "ssh_run", original)

        def ssh(host, command, ssh_args=None, timeout=40):
            commands.append((host, command))
            if command.startswith("docker ps"):
                return "web-app\tUp 3 days\nml-api\tUp 2 hours\n"
            return "INFO arranque\nERROR conexión rechazada a la base\n"

        plugin.core.ssh_run = ssh
        command = plugin.ClaudeRemoteLogsCommand(self.window)
        command.run()
        items, on_select = self.window.quick[-1]
        self.assertEqual(items, [["web-app", "Up 3 days"], ["ml-api", "Up 2 hours"]])
        on_select(-1)
        self.assertEqual(FakeRunner.calls, [])
        on_select(1)
        self.assertEqual(commands[-1], ("servidor", "docker logs --tail 50 ml-api 2>&1"))
        self.assertIn("últimos 50 registros de `ml-api` en el servidor `servidor`", self.last.prompt)
        self.assertIn("<logs>\nINFO arranque\nERROR conexión rechazada", self.last.prompt)
        self.assertEqual(self.last.option("--tools"), "Read,Grep,Glob")
        self.assertIn("## Logs · ml-api\n", self.chat.text)
        self.last.play()

        def broken(host, command, ssh_args=None, timeout=40):
            raise RuntimeError("Permission denied (publickey)")

        plugin.core.ssh_run = broken
        plugin.ClaudeRemoteLogsCommand(self.window).run()
        self.assertIn("No se pudo consultar servidor:\n\nPermission denied (publickey)", errors[-1])

    def test_terminal_usa_terminus_si_esta_instalado(self):
        original = (plugin._has_terminus, plugin.subprocess.Popen, plugin.core.find_terminal)
        self.addCleanup(lambda: (setattr(plugin, "_has_terminus", original[0]),
                                 setattr(plugin.subprocess, "Popen", original[1]),
                                 setattr(plugin.core, "find_terminal", original[2])))
        launched = []
        plugin.subprocess.Popen = lambda command, **kwargs: launched.append(command)
        plugin.core.find_terminal = lambda configured=None: "/bin/xterm"
        plugin._has_terminus = lambda: True
        plugin.ClaudeTerminalCommand(self.window).run(with_file=True)
        name, args = self.window.commands[-1]
        self.assertEqual(name, "terminus_open")
        self.assertEqual(args["cmd"], ["/bin/claude", "Voy a trabajar en @src/a.py. Léelo y espera mis instrucciones."])
        self.assertEqual(args["cwd"], "/p/app")
        self.assertEqual(launched, [])
        plugin._has_terminus = lambda: False
        plugin.ClaudeTerminalCommand(self.window).run()
        self.assertEqual(len(self.window.commands), 1)
        self.assertEqual(launched[0][:2], ["/bin/xterm", "-e"])
        os.remove(launched[0][2])

    def test_correr_pruebas_pasa_el_fallo_a_claude(self):
        folder, path = self.project()
        ran = []
        original = (plugin.core.run_tests, plugin.core.find_project_python)
        self.addCleanup(lambda: (setattr(plugin.core, "run_tests", original[0]),
                                 setattr(plugin.core, "find_project_python", original[1])))
        plugin.core.find_project_python = lambda cwd: "/venv/python"
        plugin.ClaudeRunTestsCommand(self.window).run()
        self.assertIn("No encontré pruebas para calc.py (busqué test_calc.py y calc_test.py)",
                      errors[-1])
        os.makedirs(os.path.join(folder, "tests"))
        open(os.path.join(folder, "tests", "test_calc.py"), "w").close()

        def fake(python, target, cwd, timeout=300):
            ran.append((python, target, cwd))
            return outcome
        plugin.core.run_tests = fake
        outcome = (0, "..\n2 passed in 0.01s")
        plugin.ClaudeRunTestsCommand(self.window).run()
        self.assertEqual(ran[-1], ("/venv/python", os.path.join("tests", "test_calc.py"), folder))
        self.assertEqual(FakeRunner.calls, [])
        self.assertIn("## Pruebas · tests/test_calc.py\n— pasan: 2 passed in 0.01s\n", self.chat.text)

        outcome = (1, "def test_f():\n>   assert f() == 0\nE   ZeroDivisionError\n"
                      "=== 1 failed, 1 passed in 0.02s ===")
        self.window.focus_view(self.file)
        plugin.ClaudeRunTestsCommand(self.window).run()
        self.assertIn("Corrí `pytest tests/test_calc.py` y hay pruebas que fallan", self.last.prompt)
        self.assertIn("E   ZeroDivisionError", self.last.prompt)
        self.assertIn("no la debilites", self.last.prompt)
        self.assertEqual(self.last.option("--permission-mode"), "acceptEdits")
        self.assertIn("## Pruebas que fallan · tests/test_calc.py\n> 1 failed, 1 passed in 0.02s\n",
                      self.chat.text)
        self.last.play()
        self.assertEqual(plugin._testing, set())

        # Desde el propio archivo de pruebas se corre ese archivo.
        test_view = self.window.add(View(self.window, "", os.path.join(folder, "tests", "test_calc.py")))
        outcome = (5, "no tests ran in 0.00s")
        plugin.ClaudeRunTestsCommand(self.window).run()
        self.assertEqual(ran[-1][1], os.path.join("tests", "test_calc.py"))
        self.assertIn("no encontró pruebas", self.window.messages[-1])
        self.assertIsNotNone(test_view)

    def test_adjuntar_archivos_con_arroba(self):
        folder, path = self.project()
        open(os.path.join(folder, "util.py"), "w").close()
        plugin.ClaudeAttachFileCommand(self.window).run()
        chat = self.chat
        items, on_select = self.window.quick[-1]
        self.assertEqual(items, ["calc.py", "util.py"])
        on_select(1)
        self.assertTrue(chat.text.endswith("❯ @util.py "))
        self.assertTrue(chat.type("y "))
        plugin.ClaudeAttachFileCommand(self.window).run()
        self.window.quick[-1][1](-1)                 # cancelar deja la arroba escrita
        self.assertTrue(chat.text.endswith("❯ @util.py y @"))
        chat.type("calc.py ¿en qué se parecen?")
        chat.run_command("claude_chat_send")
        self.assertTrue(self.last.prompt.startswith("@util.py y @calc.py ¿en qué se parecen?"))
        self.assertTrue(self.last.prompt.endswith(
            "Archivos adjuntos (léelos antes de responder): `util.py`, `calc.py`"))
        self.last.play()
        self.say("sin adjuntos")
        self.assertEqual(self.last.prompt, "sin adjuntos")

    def test_historial_guarda_y_retoma_una_conversacion(self):
        plugin.ClaudeHistoryCommand(self.window).run()
        self.assertIn("todavía no hay conversaciones", self.window.messages[-1])
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.last.play()
        self.say("¿y cómo lo arreglo?")
        self.last.play([("session", "s1"), ("text_start", None), ("text", "Con un try."),
                        ("result", {"result": "x", "is_error": False})])
        first = self.chat.text
        plugin.ClaudeNewChatCommand(self.window).run()
        self.say("otra cosa distinta")
        self.last.play([("session", "s2"), ("text_start", None), ("text", "Vale."),
                        ("result", {"result": "x", "is_error": False})])

        plugin.ClaudeHistoryCommand(self.window).run()
        items, on_select = self.window.quick[-1]
        self.assertEqual([item[0] for item in items], ["otra cosa distinta", "Revisión · src/a.py"])
        self.assertEqual(items[1][1], "recién · app")
        on_select(1)
        chat = self.chat
        self.assertEqual(chat.text, first)
        self.assertEqual(chat.settings()[plugin.K_SESSION], "s1")
        self.assertFalse(chat.read_only)
        self.say("sigamos")
        self.assertEqual(self.last.option("--resume"), "s1")
        self.assertEqual(self.last.cwd, "/p/app")
        self.last.play()
        # Sigue siendo una sola entrada por sesión.
        self.assertEqual(len(plugin.core.history_list(plugin._history_dir())), 2)

    def test_elegir_modelo(self):
        plugin.ClaudeSelectModelCommand(self.window).run()
        items, on_select = self.window.quick[-1]
        self.assertEqual([item[0] for item in items], ["● Predeterminado", "Opus", "Sonnet", "Haiku"])
        on_select(-1)
        self.assertEqual(SETTINGS["model"], "")
        on_select(3)
        self.assertEqual(SETTINGS["model"], "haiku")
        self.assertEqual(saved_settings[-1], plugin.SETTINGS)
        plugin.ClaudeReviewFileCommand(self.window).run()
        self.assertEqual(self.last.option("--model"), "haiku")
        self.last.play()
        plugin.ClaudeSelectModelCommand(self.window).run()
        self.assertEqual(self.window.quick[-1][0][3][0], "● Haiku")

    def test_estado_del_servidor_no_llama_a_claude(self):
        original = plugin.core.ssh_run
        self.addCleanup(setattr, plugin.core, "ssh_run", original)
        plugin.core.ssh_run = lambda host, command, ssh_args=None, timeout=40: (
            "web\tUp 3 days\nml-api\tRestarting (1) 5 seconds ago\nviejo\tExited (137) 2 days ago\n")
        plugin.ClaudeRemoteStatusCommand(self.window).run()
        self.assertEqual(FakeRunner.calls, [])
        self.assertEqual(self.window.quick, [])
        self.assertIn("## Servidor · servidor\n⚠ ml-api — Restarting (1) 5 seconds ago\n"
                      "⚠ viejo — Exited (137) 2 days ago\n✓ 1 en marcha: web\n"
                      "Para ver por qué: «Claude: Diagnosticar logs del servidor…».\n", self.chat.text)

    def test_recursos_apuntan_a_comandos_que_existen(self):
        import json
        root = ROOT
        names = set()

        def walk(node):
            if isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                if "command" in node:
                    names.add(node["command"])
                walk(node.get("children", []))

        for name in ("Default.sublime-keymap", "Default.sublime-commands",
                     "Context.sublime-menu", "Main.sublime-menu"):
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                walk(json.load(handle))
        for name in names - {"edit_settings", "insert"}:
            cls = "".join(part.capitalize() for part in name.split("_")) + "Command"
            self.assertTrue(hasattr(plugin, cls), name)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, plugin.SYNTAX.split("/", 2)[2])))
        self.assertIn("claude_chat_send", names)


if __name__ == "__main__":
    unittest.main()

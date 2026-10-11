"""El plugin contra un Sublime simulado: no llama al CLI ni necesita el editor."""
import importlib
import os
import sys
import tempfile
import types
import unittest

PACKAGES = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    def show_quick_panel(self, items, on_select): self.quick.append((items, on_select))

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
sublime.set_timeout = lambda function, delay=0: function()
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
    remote_host="servidor", remote_log_lines=50)
sublime.load_settings = lambda name: SETTINGS
sublime_plugin = types.ModuleType("sublime_plugin")


class _Command(object):
    def __init__(self, target=None):
        self.window = self.view = target


sublime_plugin.WindowCommand = sublime_plugin.TextCommand = _Command
sublime_plugin.EventListener = sublime_plugin.ViewEventListener = _Command
sys.modules.update(sublime=sublime, sublime_plugin=sublime_plugin)
sys.path.insert(0, PACKAGES)
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
        for kind, data in script:
            self.on_event(kind, data)
        self.on_exit(code, stderr, stopped)

    def stop(self):
        self.play([], stopped=True)


plugin.core.Runner = FakeRunner
plugin.core.find_claude = lambda configured=None: "/bin/claude"
plugin._background = lambda function: function()


class ChatTest(unittest.TestCase):
    def setUp(self):
        FakeRunner.calls[:] = []
        plugin._runners.clear()
        plugin._last_file.clear()
        plugin._transcripts.clear()
        plugin._restoring.clear()
        plugin._testing.clear()
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
        self.assertIn("-    return 1/0\n+    return 0\n```\n", chat.text)
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
        root = os.path.join(PACKAGES, "ClaudeCode")
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
        self.assertTrue(os.path.isfile(os.path.join(PACKAGES, plugin.SYNTAX.split("/", 1)[1])))
        self.assertIn("claude_chat_send", names)


if __name__ == "__main__":
    unittest.main()

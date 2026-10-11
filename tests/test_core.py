import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_core as core  # noqa: E402


def line(**event):
    return json.dumps(event)


class ParseLine(unittest.TestCase):
    def test_sesion_texto_y_resultado(self):
        self.assertEqual(core.parse_line(line(type="system", subtype="init", session_id="s1")),
                         [("session", "s1")])
        delta = line(type="stream_event", parent_tool_use_id=None, event={
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "hola"}})
        self.assertEqual(core.parse_line(delta), [("text", "hola")])
        start = line(type="stream_event", event={
            "type": "content_block_start", "content_block": {"type": "text", "text": ""}})
        self.assertEqual(core.parse_line(start), [("text_start", None)])
        kind, data = core.parse_line(line(type="result", result="hola", is_error=False))[0]
        self.assertEqual((kind, data["result"]), ("result", "hola"))

    def test_el_texto_del_mensaje_completo_no_se_repite(self):
        message = line(type="assistant", message={"content": [
            {"type": "text", "text": "hola"},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}]})
        self.assertEqual(core.parse_line(message), [("tool", ("t1", "Read a.py"))])

    def test_ignora_subagentes_y_ruido(self):
        sub = line(type="stream_event", parent_tool_use_id="t9", event={
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}})
        self.assertEqual(core.parse_line(sub), [])
        self.assertEqual(core.parse_line(line(type="rate_limit_event")), [])
        self.assertEqual(core.parse_line("   "), [])
        self.assertEqual(core.parse_line("no es json"), [("raw", "no es json")])


class Helpers(unittest.TestCase):
    def test_argumentos(self):
        args = core.build_args("claude", "Read", permission_mode="acceptEdits", resume="s1",
                               model="opus", persist=False, extra=["--x"])
        self.assertEqual(args[:2], ["claude", "-p"])
        self.assertEqual(args[args.index("--tools") + 1], "Read")
        self.assertEqual(args[args.index("--resume") + 1], "s1")
        self.assertEqual(args[args.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("--no-session-persistence", args)
        self.assertEqual(args[-1], "--x")
        self.assertNotIn("--effort", args)

    def test_quita_el_bloque_de_codigo(self):
        self.assertEqual(core.strip_fences("```python\nx = 1\n```\n"), "x = 1")
        self.assertEqual(core.strip_fences("    x = 1\n"), "    x = 1")
        self.assertEqual(core.strip_fences("a = '```'"), "a = '```'")

    def test_recorte_alrededor_del_cursor(self):
        self.assertEqual(core.clip_around("abcdef", 2, 4), ("ab", "cd", "ef", False))
        head, selected, tail, truncated = core.clip_around("x" * 100, 50, 50, before=10, after=5)
        self.assertEqual((len(head), selected, len(tail), truncated), (10, "", 5, True))

    def test_carpeta_de_trabajo(self):
        self.assertEqual(core.pick_cwd("/p/app/src/a.py", ["/p", "/p/app", "/p/application"]),
                         "/p/app")
        self.assertEqual(core.pick_cwd("/otro/a.py", ["/p"]), "/otro")
        self.assertEqual(core.pick_cwd(None, ["/p"]), "/p")

    def test_guion_de_terminal(self):
        with tempfile.TemporaryDirectory() as folder:
            path = core.terminal_script("/bin/claude", "/tmp/mi proyecto", "lee @a b.py",
                                        directory=folder)
            self.assertTrue(os.access(path, os.X_OK))
            self.assertNotIn(" ", os.path.basename(path))
            with open(path, encoding="utf-8") as script:
                text = script.read()
            self.assertIn("cd '/tmp/mi proyecto' || exit 1", text)
            self.assertIn("/bin/claude 'lee @a b.py'", text)

    def test_el_guion_corre_y_se_borra(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            path = core.terminal_script("/bin/true", folder, directory=folder)
            self.assertEqual(subprocess.run([path]).returncode, 0)
            self.assertFalse(os.path.exists(path))


HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "hooks", "snapshot.py")


class Edits(unittest.TestCase):
    def hook(self, backup, cwd, tool_input):
        import subprocess
        event = json.dumps({"tool_name": "Edit", "cwd": cwd, "tool_input": tool_input})
        result = subprocess.run([sys.executable, HOOK, backup], input=event.encode())
        self.assertEqual(result.returncode, 0)

    def test_diff_y_reversion_de_una_edicion(self):
        with tempfile.TemporaryDirectory() as folder:
            backup = os.path.join(folder, "respaldo")
            existing, created = os.path.join(folder, "a.py"), os.path.join(folder, "nuevo.py")
            untouched = os.path.join(folder, "igual.py")
            for path in (existing, untouched):
                with open(path, "w") as handle:
                    handle.write("x = 1\ny = 2\n")
            self.hook(backup, folder, {"file_path": existing})
            with open(existing, "w") as handle:
                handle.write("x = 1\ny = 3\n")
            self.hook(backup, folder, {"file_path": "a.py"})       # segunda edición: no pisa
            self.hook(backup, folder, {"file_path": created})
            with open(created, "w") as handle:
                handle.write("z = 0\n")
            self.hook(backup, folder, {"file_path": untouched})    # se intentó y quedó igual
            self.hook(backup, folder, {"command": "ls"})           # sin archivo: se ignora

            diff, changed, truncated = core.edits_diff(backup, folder)
            self.assertEqual((changed, truncated), ([existing, created], False))
            self.assertIn("--- a/a.py\n+++ b/a.py\n", diff)
            self.assertIn("-y = 2\n+y = 3", diff)
            self.assertIn("--- /dev/null\n+++ b/nuevo.py\n", diff)
            self.assertEqual(core.edits_diff(backup, folder, max_lines=3)[2], True)

            core.mark_applied(backup)
            self.assertEqual(core.changed_since(backup), [])
            with open(created, "a") as handle:
                handle.write("# a mano\n")
            self.assertEqual(core.changed_since(backup), [created])

            self.assertEqual(core.revert_edits(backup), [existing, created])
            with open(existing) as handle:
                self.assertEqual(handle.read(), "x = 1\ny = 2\n")
            self.assertFalse(os.path.exists(created))
            self.assertEqual(core.edits_diff(backup, folder)[1], [])

    def test_sin_respaldo_no_hay_cambios(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(core.edits_diff(folder, folder), ("", [], False))
            self.assertEqual(core.revert_edits(folder), [])
            self.assertEqual(core.changed_since(folder), [])

    def test_el_hook_nunca_bloquea(self):
        import subprocess
        result = subprocess.run([sys.executable, HOOK, "/proc/no/se/puede"], input=b"no es json")
        self.assertEqual(result.returncode, 0)

    def test_ajustes_del_hook(self):
        settings = json.loads(core.review_settings("/usr/bin/python3", "/p/mi hook.py", "/tmp/r"))
        hook = settings["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "Edit|Write|MultiEdit|NotebookEdit")
        self.assertEqual(hook["hooks"][0]["command"], "/usr/bin/python3 '/p/mi hook.py' /tmp/r")


class GitAndRemote(unittest.TestCase):
    def test_contexto_del_commit_prefiere_lo_preparado(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                subprocess.run(("git", "-c", "user.name=t", "-c", "user.email=t@t") + args,
                               cwd=folder, check=True, capture_output=True)
            git("init", "-q")
            for name in ("a.txt", "b.txt"):
                with open(os.path.join(folder, name), "w") as handle:
                    handle.write("uno\n")
            git("add", ".")
            git("commit", "-qm", "feat: primer commit")
            for name in ("a.txt", "b.txt"):
                with open(os.path.join(folder, name), "a") as handle:
                    handle.write("dos\n")
            diff, staged, subjects = core.commit_context(folder)
            self.assertFalse(staged)
            self.assertIn("a.txt", diff)
            self.assertIn("b.txt", diff)
            self.assertEqual(subjects.strip(), "feat: primer commit")
            git("add", "a.txt")
            diff, staged, _ = core.commit_context(folder)
            self.assertTrue(staged)
            self.assertIn("a.txt", diff)
            self.assertNotIn("b.txt", diff)

    def test_servicios_y_comando_de_logs(self):
        listing = "web-app\tUp 3 days\nml-api\tUp 2 hours (healthy)\n\nmalo; rm -rf /\tUp\n"
        self.assertEqual(core.parse_services(listing),
                         [("web-app", "Up 3 days"), ("ml-api", "Up 2 hours (healthy)")])
        self.assertEqual(core.logs_command("docker logs --tail {lines} {name} 2>&1", "ml-api", 50),
                         "docker logs --tail 50 ml-api 2>&1")
        with self.assertRaises(ValueError):
            core.logs_command("docker logs {name}", "x; rm -rf /", 5)

    def test_ruff_del_entorno_del_proyecto(self):
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, ".venv", "bin", "ruff")
            os.makedirs(os.path.dirname(target))
            with open(target, "w") as handle:
                handle.write("#!/bin/sh\nprintf '%s/a.py:3:5: F821 Undefined name `x`\\nFound 1 error.\\n' "
                             "\"$PWD\"\nexit 1\n")
            os.chmod(target, 0o755)
            self.assertEqual(core.find_ruff(folder), target)
            self.assertEqual(core.ruff_diagnostics(target, "a.py", folder),
                             ["a.py:3:5: F821 Undefined name `x`"])


class Extras(unittest.TestCase):
    def tree(self, folder, *paths):
        for path in paths:
            full = os.path.join(folder, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()

    def test_busca_las_pruebas_de_un_modulo(self):
        with tempfile.TemporaryDirectory() as folder:
            self.tree(folder, "app/calc.py", "tests/test_calc.py", "tests/deep/more/test_calc.py",
                      ".venv/lib/test_calc.py", "node_modules/x/test_calc.py", "app/otro.py")
            self.assertEqual(core.find_tests_for(os.path.join(folder, "app/calc.py"), folder),
                             os.path.join(folder, "tests/test_calc.py"))
            self.assertIsNone(core.find_tests_for(os.path.join(folder, "app/otro.py"), folder))
        self.assertTrue(core.is_test_file("/p/tests/test_calc.py"))
        self.assertTrue(core.is_test_file("/p/calc_test.py"))
        self.assertFalse(core.is_test_file("/p/calc.py"))
        self.assertFalse(core.is_test_file("/p/test_datos.json"))

    def test_corre_pytest_de_verdad(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "test_ok.py"), "w") as handle:
                handle.write("def test_uno():\n    assert 1 + 1 == 2\n")
            with open(os.path.join(folder, "test_mal.py"), "w") as handle:
                handle.write("def test_dos():\n    assert 1 + 1 == 3\n")
            python = core.find_project_python(folder)
            try:
                __import__("pytest")
            except ImportError:
                self.skipTest("pytest no está en este Python")
            code, output = core.run_tests(sys.executable, "test_ok.py", folder)
            self.assertEqual(code, 0, output)
            self.assertIn("1 passed", core.last_line(output))
            code, output = core.run_tests(sys.executable, "test_mal.py", folder)
            self.assertEqual(code, 1)
            self.assertIn("assert 1 + 1 == 3", output)
            self.assertIn("1 failed", core.last_line(output))
            self.assertFalse(os.path.exists(os.path.join(folder, ".pytest_cache")))
            self.assertTrue(python)

    def test_python_del_entorno_del_proyecto(self):
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, ".venv", "bin", "python")
            os.makedirs(os.path.dirname(target))
            with open(target, "w") as handle:
                handle.write("#!/bin/sh\n")
            os.chmod(target, 0o755)
            self.assertEqual(core.find_project_python(folder), target)

    def test_archivos_adjuntos(self):
        with tempfile.TemporaryDirectory() as folder:
            self.tree(folder, "src/a.py", "src/b.py", "README.md", "node_modules/x/y.js")
            self.assertEqual(core.project_files(folder), ["README.md", "src/a.py", "src/b.py"])
            message = "compara @src/a.py con @src/b.py, y @src/a.py. Escribe a yo@correo.cl y @noexiste.py"
            self.assertEqual(core.mentioned_files(message, folder), ["src/a.py", "src/b.py"])
            self.assertEqual(core.mentioned_files("sin adjuntos", folder), [])

    def test_historial(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(core.history_list(os.path.join(folder, "nada")), [])
            for index in range(4):
                core.history_save(folder, {"session": "s{}".format(index), "cwd": "/p",
                                           "updated": 100 + index, "text": "❯ hola {}".format(index),
                                           "title": "t{}".format(index), "edit": False}, keep=3)
            core.history_save(folder, {"session": "s1", "cwd": "/p", "updated": 500,
                                       "text": "nuevo", "title": "t1", "edit": True}, keep=3)
            with open(os.path.join(folder, "roto.json"), "w") as handle:
                handle.write("{no es json")
            entries = core.history_list(folder)
            self.assertEqual([e["session"] for e in entries], ["s1", "s3", "s2"])
            self.assertEqual(entries[0]["text"], "nuevo")
            core.history_save(folder, {"session": "../fuera", "updated": 1, "text": "x"})
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(folder), "fuera.json")))
        self.assertEqual(core.history_title("## Revisión · a.py\n\ntexto"), "Revisión · a.py")
        self.assertEqual(core.history_title("\n❯ ¿qué hace esto?\n\nrespuesta"), "¿qué hace esto?")
        self.assertEqual(len(core.history_title("❯ " + "x" * 200)), 80)
        self.assertEqual(core.history_title(""), "Conversación")
        self.assertEqual([core.age_text(n) for n in (5, 120, 7200, 200000)],
                         ["recién", "hace 2 min", "hace 2 h", "hace 2 d"])

    def test_estado_de_los_servicios(self):
        problems, healthy = core.service_report([
            ("web", "Up 3 days"), ("api", "Up 2 hours (healthy)"), ("ml", "Up 5 minutes (unhealthy)"),
            ("cola", "Restarting (1) 5 seconds ago"), ("viejo", "Exited (137) 2 days ago"),
            ("nginx.service", "running")])
        self.assertEqual([name for name, _ in problems], ["ml", "cola", "viejo"])
        self.assertEqual([name for name, _ in healthy], ["web", "api", "nginx.service"])


@unittest.skipIf(os.environ.get("CLAUDE_ST_OFFLINE") or not core.find_claude(),
                 "necesita el CLI de Claude")
class EndToEnd(unittest.TestCase):
    def ask(self, prompt, tools, cwd, **options):
        events, done, exit_info = [], threading.Event(), {}

        def on_exit(code, stderr, stopped):
            exit_info.update(code=code, stderr=stderr, stopped=stopped)
            done.set()

        args = core.build_args(core.find_claude(), tools, persist=False, **options)
        core.Runner(args, cwd, prompt, lambda kind, data: events.append((kind, data)),
                    on_exit).start()
        self.assertTrue(done.wait(180), "claude no terminó")
        return events, exit_info

    def test_lee_un_archivo_y_responde_en_streaming(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "dato.txt"), "w") as handle:
                handle.write("la clave es zarzamora-42\n")
            events, exit_info = self.ask(
                "Lee dato.txt y responde sólo con la clave.", core.READ_TOOLS, folder)
        self.assertEqual(exit_info["code"], 0, exit_info["stderr"])
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[0], "session")
        self.assertEqual(kinds[-1], "result")
        self.assertIn(("tool", "Read " + os.path.join(folder, "dato.txt")),
                      [(k, d) for k, d in events if k == "tool"])
        streamed = "".join(data for kind, data in events if kind == "text")
        self.assertIn("zarzamora-42", streamed)
        self.assertIn("zarzamora-42", events[-1][1]["result"])

    def test_el_hook_respalda_lo_que_claude_edita(self):
        python = __import__("shutil").which("python3")
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as backup:
            target = os.path.join(folder, "saludo.py")
            with open(target, "w") as handle:
                handle.write("def saludo():\n    return 'hola'\n")
            _, exit_info = self.ask(
                "En el archivo `saludo.py`: cambia el texto devuelto a 'adiós'. "
                "Aplica el cambio directamente en el archivo.",
                core.EDIT_TOOLS, folder, permission_mode="acceptEdits",
                extra=["--settings", core.review_settings(python, HOOK, backup)])
            self.assertEqual(exit_info["code"], 0, exit_info["stderr"])
            diff, changed, _ = core.edits_diff(backup, folder)
            self.assertEqual(changed, [target])
            self.assertIn("-    return 'hola'", diff)
            self.assertIn("adiós", diff)
            core.revert_edits(backup)
            with open(target) as handle:
                self.assertEqual(handle.read(), "def saludo():\n    return 'hola'\n")

    def test_editar_modifica_el_archivo(self):
        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "saludo.py")
            with open(target, "w") as handle:
                handle.write("def saludo():\n    return 'hola'\n")
            _, exit_info = self.ask(
                "En el archivo `saludo.py`: cambia el texto devuelto a 'adiós'. "
                "Aplica el cambio directamente en el archivo.",
                core.EDIT_TOOLS, folder, permission_mode="acceptEdits")
            self.assertEqual(exit_info["code"], 0, exit_info["stderr"])
            with open(target) as handle:
                self.assertIn("adiós", handle.read())


if __name__ == "__main__":
    unittest.main()

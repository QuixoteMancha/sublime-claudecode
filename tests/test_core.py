import json
import os
import subprocess
import sys
import tempfile
import threading
import time
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

    def test_avisa_que_hace_mientras_no_escribe(self):
        def start(**block):
            return line(type="stream_event", event={
                "type": "content_block_start", "content_block": block})
        self.assertEqual(core.parse_line(start(type="thinking", thinking="")),
                         [("activity", "pensando…")])
        self.assertEqual(core.parse_line(start(type="tool_use", id="t1", name="Edit", input={})),
                         [("activity", "preparando Edit…")])
        done = line(type="user", message={"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]})
        self.assertEqual(core.parse_line(done), [
            ("output", {"id": "t1", "text": "ok", "error": False}),
            ("activity", "esperando la respuesta…")])
        self.assertEqual(core.parse_line(line(type="user", message={"content": "hola"})), [])
        sub = line(type="user", parent_tool_use_id="t9", message={"content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]})
        self.assertEqual(core.parse_line(sub), [])
        agent = line(type="assistant", parent_tool_use_id="t9", message={"content": [
            {"type": "tool_use", "id": "t3", "name": "Grep", "input": {"pattern": "x"}}]})
        self.assertEqual(core.parse_line(agent), [("activity", "subagente · Grep x…")])

    def test_permisos_limites_y_uso(self):
        ask = line(type="control_request", request_id="r1", request={
            "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "pytest -q"},
            "description": "Correr las pruebas"})
        kind, data = core.parse_line(ask)[0]
        self.assertEqual((kind, data["id"], data["tool"], data["summary"], data["description"]),
                         ("permission", "r1", "Bash", "Bash pytest -q", "Correr las pruebas"))
        self.assertEqual(core.parse_line(line(type="control_request", request_id="r2",
                                              request={"subtype": "otra"})), [])
        self.assertEqual(core.parse_line(line(type="control_response", response={})), [])
        info = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": 1000}
        self.assertEqual(core.parse_line(line(type="rate_limit_event", rate_limit_info=info)),
                         [("quota", info), ("limit", info)])
        self.assertEqual(core.parse_line(line(type="rate_limit_event",
                                              rate_limit_info={"status": "allowed"})), [])
        message = line(type="assistant", message={"content": [], "usage": {
            "input_tokens": 10, "cache_read_input_tokens": 70000,
            "cache_creation_input_tokens": 5000, "output_tokens": 990}})
        self.assertEqual(core.parse_line(message), [("usage", 76000)])

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

    def test_nota_del_modo_del_chat(self):
        args = core.build_args("claude", "Read", system="nota")
        self.assertEqual(args[args.index("--append-system-prompt") + 1], "nota")
        self.assertNotIn("--append-system-prompt", core.build_args("claude", "Read"))
        read = core.chat_note("Read,Grep,Glob", False)
        self.assertIn("Read, Grep, Glob", read)
        self.assertIn("sólo lectura", read)
        self.assertIn("No puedes ejecutar comandos", read)
        edit = core.chat_note("Read,Edit,Bash", True)
        self.assertIn("edición activada", edit)
        self.assertNotIn("No puedes ejecutar comandos", edit)

    def test_pliegues_de_un_diff(self):
        text = "\n".join([
            "### Cambios en 2 archivos", "```diff",
            "--- a/uno.py", "+++ b/uno.py", "@@ -1,3 +1,3 @@",
            " ```", "---- era un comentario SQL", "+nueva",
            "--- /dev/null", "+++ b/dos.py", "@@ -0,0 +1,2 @@", "+a", "+b",
            "```", "+ esto ya no es diff"])
        folds = core.diff_folds(text)
        self.assertEqual([fold[2:] for fold in folds], [(1, 1), (2, 0)])
        self.assertEqual(
            [text[a:b] for a, b, _, _ in folds],
            ["\n@@ -1,3 +1,3 @@\n ```\n---- era un comentario SQL\n+nueva",
             "\n@@ -0,0 +1,2 @@\n+a\n+b"])
        self.assertEqual(core.diff_folds("```diff\n--- a/x\n+++ b/x\n```"), [])
        self.assertEqual(core.diff_folds("sin diff"), [])

    def test_proceso_persistente_y_modos(self):
        args = core.build_args("claude", "Read", live=True)
        self.assertEqual(args[args.index("--input-format") + 1], "stream-json")
        self.assertEqual(args[args.index("--permission-prompt-tool") + 1], "stdio")
        self.assertNotIn("--input-format", core.build_args("claude", "Read"))
        self.assertEqual(core.join_tools("Read, Edit", "Bash,Read", ""), "Read,Edit,Bash")
        plan = core.chat_note("Read,Grep", False, plan=True)
        self.assertIn("modo plan", plan)
        self.assertNotIn("sólo lectura", plan)
        asks = core.chat_note("Read,Edit,Bash", True, asks=True)
        self.assertIn("aprueba cada uno", asks)
        self.assertNotIn("aprueba cada uno", core.chat_note("Read,Edit,Bash", True))

    def test_web_metodo_y_permisos_por_regla(self):
        args = core.build_args("claude", "Read", model="opus", fallback="sonnet",
                               allowed=["WebSearch", "", "Bash(git log *)"], extra=["--x"])
        start = args.index("--allowedTools")
        self.assertEqual(args[start + 1:], ["WebSearch", "Bash(git log *)", "--x"])
        self.assertEqual(args[args.index("--fallback-model") + 1], "sonnet")
        plain = core.build_args("claude", "Read", model="opus", fallback="opus", allowed=[])
        self.assertNotIn("--allowedTools", plain)
        self.assertNotIn("--fallback-model", plain)

        read = core.method_note("Read,Grep,Glob")
        self.assertIn("No puedes ejecutar nada", read)
        self.assertNotIn("WebSearch", read)
        full = core.method_note("Read,Bash,WebSearch,WebFetch,Agent")
        for expected in ("reproduce el fallo", "da la URL", "subagente", "`ruta:línea`",
                         "fuera del proyecto",
                         "al menos dos alternativas", "línea base"):
            self.assertIn(expected, full)
        self.assertNotIn("línea base", read)
        watch = core.chat_note("Read,Bash", False, asks=True)
        self.assertIn("son para observar", watch)
        self.assertNotIn("son para observar", core.chat_note("Read,Edit,Bash", True, asks=True))

        rule = core.permission_rule
        self.assertEqual(rule("Bash", {"command": "pytest  -q tests"}), ("Bash", "pytest"))
        self.assertEqual(rule("Bash", {"command": "git log --oneline"}), ("Bash", "git log"))
        for command in ("pytest -q; rm -rf x", "ls | sh", "python3 -c 'x'", "git --version",
                        "A=1 pytest", "echo $(id)", "ls > x", "echo 'sin cerrar", "",
                        "/usr/bin/sudo ls", "ls\nrm x"):
            self.assertIsNone(rule("Bash", {"command": command}), command)
        self.assertEqual(rule("WebFetch", {"url": "https://Docs.Python.org/3/x"}),
                         ("WebFetch", "docs.python.org"))
        self.assertIsNone(rule("WebFetch", {}))
        self.assertEqual(rule("Edit", {"file_path": "/x"}), ("Edit", ""))
        self.assertEqual(core.rule_label(("Bash", "git log")), "Bash git log …")
        self.assertEqual(core.rule_label(("Edit", "")), "Edit")
        allowed = {("Bash", "pytest"), ("WebSearch", ""), "Read"}
        self.assertTrue(core.rule_allows(allowed, "Bash", {"command": "pytest -x"}))
        self.assertFalse(core.rule_allows(allowed, "Bash", {"command": "pytest -x && ls"}))
        self.assertFalse(core.rule_allows(allowed, "Bash", {"command": "ls"}))
        self.assertTrue(core.rule_allows(allowed, "WebSearch", {"query": "x"}))
        self.assertTrue(core.rule_allows(allowed, "Read", {}))
        self.assertEqual(core.tool_summary("Agent", {"description": "busca usos"}),
                         "Agent busca usos")

    def test_salidas_razonamiento_y_citas_plegables(self):
        delta = line(type="stream_event", event={
            "type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "a ver"}})
        self.assertEqual(core.parse_line(delta), [("thinking", "a ver")])
        done = line(type="user", message={"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": [{"type": "text", "text": "exit 1\n"}, {"type": "image"}]},
            {"type": "tool_result", "tool_use_id": "t2", "content": "  \n"}]})
        self.assertEqual(core.parse_line(done), [
            ("output", {"id": "t1", "text": "exit 1", "error": True}),
            ("activity", "esperando la respuesta…")])
        tools = {}
        self.assertEqual(core._untag(tools, "tool", ("t1", "Bash ls")), (True, "Bash ls"))
        self.assertEqual(core._untag(tools, "tool", ("t1", "Bash ls")), (False, None))
        self.assertEqual(core._untag(tools, "output", {"id": "t1", "text": "x"}),
                         (True, {"id": "t1", "text": "x", "tool": "Bash ls"}))
        self.assertEqual(core._untag(tools, "text_start", None), (True, None))

        block = core.quote_block("  ↳ salida · Bash ls", "a\n\nb\n")
        self.assertEqual(block, "  ↳ salida · Bash ls\n  │ a\n  │\n  │ b\n")
        long = core.quote_block("  ↳ x", "\n".join(str(n) for n in range(500)), max_lines=50,
                                tail=10)
        self.assertIn("  │ 39\n  │ … 450 líneas omitidas …\n  │ 490\n", long)
        self.assertTrue(long.endswith("  │ 499\n"))
        text = "hola\n" + block + "\ntexto\n  ✻ razonamiento\n  │ pienso\n  ↳ sin cuerpo\nfin"
        self.assertEqual([text[a:b] for a, b in core.quote_folds(text)],
                         ["\n  │ a\n  │\n  │ b", "\n  │ pienso"])
        self.assertEqual(core.quote_folds("  │ suelta\nnada"), [])

        folder = tempfile.mkdtemp(prefix="claude-st-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        open(os.path.join(folder, "a.py"), "w").close()
        self.assertEqual(
            core.mentioned_files("mira @a.py:10-40, @a.py:7 y @b.py:3 y @a.py.", folder),
            ["a.py:10-40", "a.py:7", "a.py"])

    def test_tareas_vista_previa_y_enlaces(self):
        result = {"modelUsage": {"m": {"contextWindow": 200000}}}
        self.assertEqual(core.context_percent(170000, result), 85.0)
        self.assertIsNone(core.context_percent(170000, {}))
        self.assertIsNone(core.context_percent(0, result))

        self.assertEqual(core.tool_summary("TaskUpdate", {"taskId": "2", "status": "completed"}),
                         "TaskUpdate #2 completed")
        tasks = []
        self.assertEqual(core.task_line("TaskCreate Leer el módulo", tasks), "☐ Leer el módulo")
        self.assertEqual(core.task_line("TaskCreate Corregir", tasks), "☐ Corregir")
        self.assertEqual(core.task_line("TaskUpdate #2 in_progress", tasks), "▸ Corregir")
        self.assertEqual(core.task_line("TaskUpdate #1 completed", tasks), "☑ Leer el módulo")
        self.assertEqual(core.task_line("TaskUpdate #7 completed", tasks), "☑ tarea #7")
        self.assertIsNone(core.task_line("TaskUpdate #1 rara", tasks))
        self.assertIsNone(core.task_line("Bash ls", tasks))

        self.assertEqual(
            core.edit_preview("Edit", {"file_path": "/etc/x.conf", "old_string": "a\nb",
                                       "new_string": "a\nc"}),
            "--- a/etc/x.conf\n+++ b/etc/x.conf\n@@ -1,2 +1,2 @@\n a\n-b\n+c")
        folder = tempfile.mkdtemp(prefix="claude-st-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        path = os.path.join(folder, "a.txt")
        with open(path, "w") as handle:
            handle.write("uno\n")
        written = core.edit_preview("Write", {"file_path": path, "content": "uno\ndos\n"})
        self.assertTrue(written.endswith("@@ -1 +1,2 @@\n uno\n+dos"))
        new = core.edit_preview("Write", {"file_path": path + ".nuevo", "content": "x\n" * 200},
                                max_lines=10)
        self.assertTrue(new.endswith("… 193 líneas más"))
        self.assertEqual(core.edit_preview("Bash", {"command": "ls"}), "")
        self.assertEqual(core.edit_preview("Edit", {"file_path": "/x"}), "")

        line_ = "Según https://docs.python.org/3/library/os.html, y (https://a.b/c)."
        self.assertEqual(core.url_at(line_, 10), "https://docs.python.org/3/library/os.html")
        self.assertEqual(core.url_at(line_, line_.index("a.b")), "https://a.b/c")
        self.assertIsNone(core.url_at(line_, 2))

    @unittest.skipUnless(__import__("shutil").which("git"), "sin git")
    def test_foto_de_git_y_lo_que_cambio_despues(self):
        folder = tempfile.mkdtemp(prefix="claude-st-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        self.assertIsNone(core.git_checkpoint(os.path.join(folder, "no-existe")))

        def git(*args):
            subprocess.run(("git", "-c", "user.name=x", "-c", "user.email=x@x") + args,
                           cwd=folder, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)

        def write(name, text):
            with open(os.path.join(folder, name), "w") as handle:
                handle.write(text)

        git("init", "-q")
        git("config", "user.name", "x")
        git("config", "user.email", "x@x")
        write("a.py", "1\n")
        write("b.py", "1\n")
        git("add", ".")
        git("commit", "-qm", "inicio")
        clean = core.git_checkpoint(folder)
        self.assertEqual(core.git_drift(folder, clean), ([], []))
        write("a.py", "2\n")
        write("b.py", "2\n")
        write("nuevo.txt", "n\n")
        self.assertEqual(core.git_drift(folder, clean, [os.path.join(folder, "b.py")]),
                         (["a.py"], ["nuevo.txt"]))
        # Con cambios sin confirmar, la foto es la de ese momento y no HEAD.
        dirty = core.git_checkpoint(folder)
        self.assertNotEqual(dirty["ref"], clean["ref"])
        write("b.py", "3\n")
        self.assertEqual(core.git_drift(folder, dirty), (["b.py"], []))
        git("checkout", dirty["ref"], "--", ".")
        with open(os.path.join(folder, "b.py")) as handle:
            self.assertEqual(handle.read(), "2\n")
        self.assertEqual(core.git_drift(folder, None), ([], []))

    def test_modelo_segun_la_tarea_y_el_limite_de_uso(self):
        used = line(type="rate_limit_event", rate_limit_info={
            "status": "allowed", "rateLimitType": "seven_day", "utilization": 0.4})
        self.assertEqual([kind for kind, _ in core.parse_line(used)], ["quota"])
        quota = {}
        core.quota_update(quota, {"status": "allowed"})
        core.quota_update(quota, {"status": "allowed", "rateLimitType": "seven_day",
                                  "utilization": 0.4, "resetsAt": 5000})
        self.assertEqual(quota, {"seven_day": {"used": 40.0, "resets": 5000}})
        self.assertEqual(core.quota_used({"utilization": 82}), 82.0)
        self.assertEqual(core.quota_used({"status": "rejected"}), 100.0)

        level = core.task_level
        self.assertEqual(level("¿qué hace esta función?"), 0)
        self.assertEqual(level("cambia el nombre de la variable", "acceptEdits"), 1)
        self.assertEqual(level("x" * 300), 1)
        self.assertEqual(level("¿por qué falla el login?"), 2)
        self.assertEqual(level("Refactoriza el módulo de pagos", "acceptEdits"), 2)
        self.assertEqual(level("hola", "plan"), 2)
        self.assertEqual(level("x" * 2000), 2)
        # Investigar afuera, o crear u optimizar algo de ML, también es exigente.
        for text in ("busca en la web cómo hacerlo", "entrena un clasificador de spam",
                     "mejora el recall del modelo de ML", "¿qué dice la documentación oficial?",
                     "ajusta los hiperparámetros", "compara con el estado del arte"):
            self.assertEqual(level(text), 2, text)
        # «modelo» a secas suele ser el de una base de datos.
        self.assertEqual(level("agrega un campo al modelo Aviso"), 0)
        self.assertEqual(level("abre el html"), 0)

        pick = lambda text, mode=None, quota=None, **options: core.pick_model(
            text, mode, quota or {}, now=1000, **options)
        self.assertEqual(pick("¿qué hace esta función?"), ("haiku", "tarea simple"))
        self.assertEqual(pick("renombra x", "acceptEdits"), ("sonnet", "tarea corriente"))
        self.assertEqual(pick("depura el fallo"), ("opus", "tarea exigente"))
        self.assertEqual(pick("optimiza el entrenamiento del modelo"),
                         ("opus", "investigación y diseño"))
        self.assertEqual(pick("diseña la arquitectura consultando fuentes externas"),
                         ("opus", "investigación y diseño"))
        # La conversación no baja de modelo por una pregunta simple, pero sí sube.
        self.assertEqual(pick("gracias", current="opus"), ("opus", "sigue la conversación"))
        self.assertEqual(pick("depura el fallo", current="haiku"), ("opus", "tarea exigente"))
        self.assertEqual(pick("hola", current="otro"), ("haiku", "tarea simple"))
        # Cerca del límite se ahorra; el que ya se restableció no cuenta.
        week = {"seven_day": {"used": 80.0, "resets": 5000}}
        self.assertEqual(pick("depura el fallo", quota=week),
                         ("sonnet", "límite semanal al 80 %"))
        self.assertEqual(pick("depura el fallo", quota=week, current="opus"),
                         ("sonnet", "límite semanal al 80 %"))
        self.assertEqual(pick("hola", quota=week), ("haiku", "tarea simple"))
        session = {"five_hour": {"used": 95.0, "resets": 5000}}
        self.assertEqual(pick("depura el fallo", quota=session),
                         ("haiku", "límite de 5 horas al 95 %"))
        self.assertEqual(pick("depura el fallo", quota={"five_hour": {"used": 95.0, "resets": 900}}),
                         ("opus", "tarea exigente"))
        # El límite propio de Opus sólo saca a Opus.
        opus = {"seven_day_opus": {"used": 99.0, "resets": None}}
        self.assertEqual(pick("depura el fallo", quota=opus),
                         ("sonnet", "límite semanal de Opus al 99 %"))
        self.assertEqual(pick("depura el fallo", quota=week, limits=[85]),
                         ("opus", "tarea exigente"))
        self.assertEqual(pick("depura el fallo", models=["sonnet"]),
                         ("sonnet", "tarea simple"))

    def test_contexto_y_limite_de_uso(self):
        result = {"modelUsage": {"a": {"contextWindow": 200000}, "b": {"contextWindow": 1000}}}
        self.assertEqual(core.context_text(76000, result), "contexto 38 %")
        self.assertEqual(core.context_text(76000, {}), "contexto 76k")
        self.assertEqual(core.context_text(None, result), "")
        now = time.mktime((2026, 10, 12, 10, 0, 0, 0, 0, -1))
        later = time.mktime((2026, 10, 12, 18, 30, 0, 0, 0, -1))
        tomorrow = time.mktime((2026, 10, 14, 9, 5, 0, 0, 0, -1))
        self.assertEqual(
            core.limit_text({"status": "rejected", "rateLimitType": "five_hour",
                             "resetsAt": later}, now),
            "límite de uso de 5 horas alcanzado; se restablece a las 18:30")
        self.assertEqual(
            core.limit_text({"status": "allowed_warning", "rateLimitType": "seven_day",
                             "utilization": 0.92, "resetsAt": tomorrow}, now),
            "límite de uso semanal al 92 %; se restablece el 14-10 a las 09:05")
        self.assertEqual(core.limit_text({"status": "allowed_warning"}, now),
                         "límite de uso cerca de agotarse")
        self.assertEqual(core.limit_text({"status": "allowed"}, now), "")

    def test_diff_del_bufer_contra_el_disco(self):
        disk = "".join("linea {}\n".format(n) for n in range(200))
        text = disk.replace("linea 100\n", "linea cien\n")
        diff = core.buffer_diff(disk, text, "a.py")
        self.assertIn("--- a/a.py\n+++ b/a.py\n", diff)
        self.assertIn("-linea 100\n+linea cien", diff)
        self.assertLess(len(diff), len(text) // 4)
        self.assertIsNone(core.buffer_diff(disk, disk, "a.py"))
        self.assertIsNone(core.buffer_diff(None, text, "a.py"))
        # Si el diff no ahorra nada, mejor el archivo entero.
        self.assertIsNone(core.buffer_diff("a\n", "b\n", "a.py"))

    def test_pliegues_del_codigo_largo(self):
        body = ["linea {}".format(n) for n in range(10)]
        text = "\n".join(["Mira:", "```python"] + body + ["```", "y esto:", "```", "corto", "```",
                         "```diff"] + body + ["```"])
        folds = core.code_folds(text, 6)
        self.assertEqual(len(folds), 1)
        a, b = folds[0]
        self.assertEqual(text[a:b], "\n" + "\n".join(body[4:]))
        self.assertEqual(core.code_folds(text, 10), [])
        self.assertEqual(core.code_folds(text, 0), [])

    def test_un_diff_largo_conserva_todos_los_archivos(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as backup:
            for number in range(12):
                path = os.path.join(folder, "m{:02}.py".format(number))
                with open(path, "w") as handle:
                    handle.write("".join("vieja {}\n".format(n) for n in range(60)))
                event = json.dumps({"tool_name": "Edit", "cwd": folder,
                                    "tool_input": {"file_path": path}})
                subprocess.run([sys.executable, HOOK, backup], input=event.encode(), check=True)
                with open(path, "w") as handle:
                    handle.write("".join("nueva {}\n".format(n) for n in range(60)))
            report = core.edits_report(backup, folder)
            self.assertEqual(len(report["changed"]), 12)
            self.assertTrue(report["truncated"])
            # Los doce figuran, cada uno con su comienzo y cuánto falta.
            self.assertEqual(report["diff"].count("\n+++ b/m"), 12)
            self.assertIn("\n… 90 líneas más\n--- a/m01.py", report["diff"])
            self.assertLessEqual(len(report["diff"].splitlines()), 400 + 12)
            # Los totales son los de la edición completa, no los de lo que se muestra.
            self.assertEqual((report["added"], report["removed"]), (720, 720))
            folds = core.diff_folds("```diff\n{}\n```".format(report["diff"]))
            self.assertEqual(len(folds), 12)

    def test_revertir_un_solo_archivo(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as root:
            backup = os.path.join(root, "vivo")
            paths = [os.path.join(folder, name) for name in ("uno.py", "dos.py")]
            for path in paths:
                with open(path, "w") as handle:
                    handle.write("antes\n")
                event = json.dumps({"tool_name": "Edit", "cwd": folder,
                                    "tool_input": {"file_path": path}})
                subprocess.run([sys.executable, HOOK, backup], input=event.encode(), check=True)
                with open(path, "w") as handle:
                    handle.write("despues\n")
            self.assertEqual(core.edited_files(backup), paths)
            final = core.rotate_backup(backup, os.path.join(root, "turno"))
            self.assertEqual((os.listdir(backup), core.edited_files(final)), ([], paths))
            core.mark_applied(final)
            self.assertEqual(core.revert_edits(final, [paths[1]]), [paths[1]])
            with open(paths[0]) as one, open(paths[1]) as two:
                self.assertEqual((one.read(), two.read()), ("despues\n", "antes\n"))
            # Lo ya revertido no figura como pendiente ni como «cambió después».
            self.assertEqual(core.edited_files(final), [paths[0]])
            self.assertEqual(core.changed_since(final), [])

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


FAKE_CLI = r'''
import json, sys
def out(**event):
    sys.stdout.write(json.dumps(event) + "\n"); sys.stdout.flush()
turn = 0
for raw in sys.stdin:
    event = json.loads(raw)
    if event["type"] == "control_request":
        out(type="control_response", response={"subtype": "success",
            "request_id": event["request_id"], "response": {"still_queued": []}})
        out(type="result", result="", is_error=False, subtype="interrupted")
        continue
    if event["type"] != "user":
        continue
    turn += 1
    text = event["message"]["content"]
    out(type="system", subtype="init", session_id="s1")
    if text == "cuelga":
        continue
    if text.startswith("corre"):
        out(type="control_request", request_id="p%d" % turn, request={
            "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"}})
        answer = json.loads(sys.stdin.readline())["response"]["response"]
        text = "%s %s" % (answer["behavior"], json.dumps(answer.get("updatedInput")))
    out(type="stream_event", event={"type": "content_block_delta",
        "delta": {"type": "text_delta", "text": "%d:%s" % (turn, text)}})
    out(type="result", result=text, is_error=False)
'''


class LiveSession(unittest.TestCase):
    """La sesión persistente contra un CLI de mentira que habla el mismo protocolo."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="claude-st-live-")
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)
        script = os.path.join(self.folder, "cli.py")
        with open(script, "w") as handle:
            handle.write(FAKE_CLI)
        self.session = core.Session([sys.executable, script], self.folder)
        self.session.start()
        self.addCleanup(self.session.close)

    def ask(self, prompt, on_permission=None, stop=False):
        events, done, exit_info = [], threading.Event(), {}

        def on_event(kind, data):
            events.append((kind, data))
            if kind == "permission" and on_permission:
                on_permission(data)

        def on_exit(code, stderr, stopped):
            exit_info.update(code=code, stopped=stopped)
            done.set()

        turn = self.session.ask(prompt, on_event, on_exit)
        if stop:
            turn.stop()
        self.assertTrue(done.wait(20), "el turno no terminó")
        return events, exit_info

    def test_un_mismo_proceso_atiende_varios_mensajes(self):
        pid = self.session.proc.pid
        events, exit_info = self.ask("hola")
        self.assertEqual(exit_info, {"code": 0, "stopped": False})
        self.assertEqual(events[0], ("session", "s1"))
        self.assertIn(("text", "1:hola"), events)
        events, _ = self.ask("otra vez")
        self.assertIn(("text", "2:otra vez"), events)
        self.assertTrue(self.session.alive())
        self.assertEqual(self.session.proc.pid, pid)
        self.assertFalse(self.session.busy())

    def test_pide_permiso_y_recibe_la_respuesta(self):
        def allow(data):
            self.assertEqual((data["tool"], data["summary"]), ("Bash", "Bash ls"))
            self.session.answer(data["id"], True, data["input"])
        events, _ = self.ask("corre ls", allow)
        self.assertIn(("text", '1:allow {"command": "ls"}'), events)
        events, _ = self.ask("corre ls", lambda data: self.session.answer(data["id"], False))
        self.assertIn(("text", "2:deny null"), events)
        # Lo permitido para toda la conversación ya no se pregunta.
        self.session.allowed.add(("Bash", "ls"))
        events, _ = self.ask("corre ls", lambda data: self.fail("no debía preguntar"))
        self.assertIn(("text", '3:allow {"command": "ls"}'), events)
        self.assertNotIn("permission", [kind for kind, _ in events])

    def test_detener_interrumpe_el_turno_sin_matar_el_proceso(self):
        _, exit_info = self.ask("cuelga", stop=True)
        self.assertEqual(exit_info, {"code": 0, "stopped": True})
        self.assertTrue(self.session.alive())
        events, _ = self.ask("sigo")
        self.assertIn(("text", "2:sigo"), events)

    def test_si_el_proceso_muere_el_turno_termina_con_su_codigo(self):
        done, exit_info = threading.Event(), {}
        self.session.ask("cuelga", lambda kind, data: None,
                         lambda code, err, stopped: (exit_info.update(code=code), done.set()))
        self.session.close()
        self.assertTrue(done.wait(20))
        self.assertNotEqual(exit_info["code"], 0)
        self.assertFalse(self.session.alive())
        # Un mensaje a un proceso muerto no deja el chat esperando para siempre.
        again = {}
        self.session.ask("hola", lambda kind, data: None,
                         lambda code, err, stopped: again.update(code=code, err=err))
        self.assertEqual(again["code"], 1)

    def test_un_cli_que_no_arranca_no_deja_eventos(self):
        session = core.Session([sys.executable, "-c", "import sys; sys.exit(3)"], self.folder)
        session.start()
        done, exit_info = threading.Event(), {}
        session.ask("hola", lambda kind, data: None,
                    lambda code, err, stopped: (exit_info.update(code=code), done.set()))
        self.assertTrue(done.wait(20))
        self.assertEqual((session.events, bool(exit_info["code"])), (0, True))


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

    def test_la_sesion_viva_recuerda_y_ejecuta_con_permiso(self):
        with tempfile.TemporaryDirectory() as folder:
            args = core.build_args(core.find_claude(), "Read,Bash",
                                   permission_mode="acceptEdits", persist=False, live=True)
            session = core.Session(args, folder)
            session.start()
            self.addCleanup(session.close)

            def ask(prompt):
                events, asked, done, info = [], [], threading.Event(), {}

                def on_event(kind, data):
                    events.append((kind, data))
                    if kind == "permission":
                        asked.append(data["tool"])
                        session.answer(data["id"], True, data["input"])

                def on_exit(code, stderr, stopped):
                    info.update(code=code, stderr=stderr)
                    done.set()

                session.ask(prompt, on_event, on_exit)
                self.assertTrue(done.wait(180), "claude no terminó")
                self.assertEqual(info["code"], 0, info["stderr"])
                return events, asked

            ask("Recuerda esta clave: zarzamora-42. Responde sólo «anotado».")
            pid = session.proc.pid
            events, _ = ask("¿Cuál era la clave? Responde sólo con la clave.")
            self.assertIn("zarzamora-42", events[-1][1]["result"])
            self.assertEqual(session.proc.pid, pid)
            events, asked = ask("Ejecuta con Bash exactamente `echo hola-$((40+2))` y responde "
                                "sólo con lo que imprime.")
            self.assertIn("hola-42", events[-1][1]["result"])
            # Si tus ajustes de Claude Code ya permiten ese comando, no pregunta.
            self.assertIn(asked, ([], ["Bash"]))

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

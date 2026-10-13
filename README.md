# ClaudeCode para Sublime Text

Llama a Claude Code (`claude`) desde el editor. Requiere Sublime Text 4 y el CLI ya autenticado.

## El chat

`Ctrl+Alt+C`, `A` abre el chat en una columna a la derecha. La conversación queda arriba y se
escribe después de la marca `❯`:

- `Enter` envía; `Shift+Enter` agrega una línea.
- Sobre la marca hay una barra con tres desplegables —**modo**, **modelo** y **esfuerzo**—: un
  clic abre la lista y otro elige. Hacen lo mismo que sus comandos; `"chat_controls": false`
  la quita.
- El archivo en que estabas trabajando (y su selección) viaja como contexto cuando cambia.
- Todos los comandos responden en el chat, así que se les puede contestar ahí.
- Doble clic sobre una cita `ruta:línea` abre el archivo; sobre una URL, el navegador.
- Las tareas que Claude se anota en un trabajo largo se ven como lista: `☐` pendiente, `▸` en
  curso, `☑` hecha.
- `@` abre la lista de archivos del proyecto y deja escrito `@ruta`; Claude lee los adjuntos
  antes de responder (`Esc` deja la arroba sola).
- Cada conversación se guarda al terminar un turno; «Conversaciones anteriores…» la devuelve al
  chat y la continúa. Se conservan las 50 más recientes, en `Packages/User/ClaudeCode/history`.
- El chat tiene tres modos, que se eligen con `Ctrl+Alt+C`, `U`: **sólo lectura** (el de
  partida), **plan** (investiga y propone, sin tocar nada; la pestaña se llama «Claude ◇») y
  **edición** («Claude ✎»). Los comandos que editan la activan solos. Tras un plan, la barra
  ofrece **▶ Aplicar el plan**: pasa a edición y lo pide (lo que haya a medio escribir se suma
  como ajuste).
- La salida de cada comando queda escrita bajo la herramienta, plegada (`↳ salida · Bash pytest
  -q`), y lo mismo el razonamiento cuando el modelo lo entrega (`✻ razonamiento`). Se abren con
  su control. `"tool_output"` elige de qué herramientas y `"show_thinking"` lo apaga.
- Un mensaje que empieza con `/` y un nombre viaja tal cual a Claude Code: sirve para sus
  comandos y skills (`/compact`, `/code-review`…). «Compactar la conversación» (`Ctrl+Alt+C`,
  `X`) resume lo conversado para liberar contexto sin empezar de cero.
- **Modelo automático.** Con «Automático» en el desplegable de modelo, el plugin elige en cada
  mensaje: el más económico para una pregunta corta, el intermedio para el trabajo corriente y
  el más capaz para planes, diagnósticos, diseño, revisiones y refactors, y para lo que pida
  investigar en fuentes externas o crear u optimizar algo de aprendizaje automático
  (`· opus (investigación y diseño)`). Cerca de los límites
  de uso del plan (de 5 horas o semanal) baja un escalón desde el 75 % y dos desde el 90 %.
  Una conversación no baja de modelo porque la pregunta siguiente sea simple —cambiarlo obliga
  a releer todo sin caché—: sólo sube si la tarea lo pide o baja si el límite obliga. El cierre
  del turno dice cuál usó y por qué (`· sonnet (límite semanal al 80 %)`). Se ajusta con
  `"auto_models"` y `"auto_model_limits"`.
- **Esfuerzo automático.** La misma opción existe en el desplegable de esfuerzo: bajo, medio o
  alto según el nivel de la tarea (`"auto_efforts"`), con el mismo ahorro.
- **Ahorro según el plan.** Con suscripción, el modo automático mira los límites de 5 horas y
  semanal, y también el ritmo: si va usado bastante más que la parte del período que ya pasó
  (`"auto_pace_margin"`), ahorra desde ya para no agotarlo antes de que se restablezca. Con
  clave de API lleva la cuenta de lo gastado en el día (`· US$ 0.12 · hoy US$ 1.40 de 5.00`) y
  trata `"api_daily_budget"` como un límite más.
- **Consulta antes de subir.** Si la tarea pide más de lo que ese ahorro permite, un panel
  pregunta: seguir ahorrando (y no volver a preguntar en la conversación), subir sólo esta vez
  o subir en toda la conversación; cerrarlo es ahorrar. Cuando se ahorra, Claude lo sabe: va a
  lo esencial y avisa si la tarea necesita más. `"ask_before_raising": false` baja sin
  preguntar.
- Desde el 80 % de la ventana de contexto la barra ofrece **⚠ contexto 84 % · compactar**
  (`"context_warning"`).
- `@ruta:10-40` adjunta sólo ese tramo del archivo. El contexto incluye también qué otros
  archivos del proyecto están abiertos.
  `"chat_edit_default": true` hace que nazca pudiendo editar. Cada turno termina indicando el
  modo (`— listo (12 s) · sólo lectura`) y Claude sabe en cuál está, así que avisa cómo
  habilitar la edición en vez de limitarse a mostrar el código.
- En cualquier modo Claude puede **buscar en la web y leer páginas** (`"web_tools"`), delegar
  una búsqueda amplia en un subagente (`"agent_tools"`) y sigue un método de trabajo: investiga
  antes de responder, reproduce y verifica cuando puede ejecutar, cita `ruta:línea` y da sus
  fuentes (`"work_method"`; `"custom_instructions"` suma las tuyas). Para diseñar una
  arquitectura o algo de aprendizaje automático, o mejorar un modelo ya hecho, el método le pide
  investigar afuera primero, comparar alternativas y fijar métrica y línea base antes de tocar. La web no pide permiso
  (`"allowed_tools"`, donde también caben reglas como `"Bash(git log *)"`).
- Si cambias de modo a mitad de una conversación, el modo nuevo viaja con el mensaje siguiente.
- Sin selección, el contexto incluye la línea del cursor.
- Mientras Claude piensa, prepara una herramienta o espera, el chat y la barra de estado lo
  dicen, con los segundos que lleva (`⋯ pensando… 12 s`); la línea desaparece cuando llega la
  respuesta. Si trabaja un subagente, muestra qué herramienta usa.
- `Enter` mientras Claude responde deja el mensaje en cola: se envía solo
  al terminar. Si la respuesta se detiene o falla, lo que estaba en cola vuelve al borrador.
- «Abrir otro chat» (`Ctrl+Alt+C`, `Y`) abre una conversación más, que puede correr mientras la
  primera responde. Los comandos responden en el último chat que se usó.
- El cierre de cada turno dice cuánto de la ventana de contexto va usado
  (`— listo (12 s) · sólo lectura · contexto 38 %`); cerca del 100 % conviene un chat nuevo.
  Los avisos de límite de uso del plan se anotan en el chat con la hora en que se restablece.
- Un bloque de código largo de una respuesta llega plegado (`"fold_code_lines"`), igual que los
  diffs. Cada bloque plegable trae su control —`▸ mostrar 38 líneas` / `▾ plegar`— para abrirlo
  o cerrarlo por separado con un clic (`"fold_controls"`); `Ctrl+Alt+C`, `K` sigue abriendo o
  cerrando todos a la vez.
- Con cambios sin guardar viaja el diff contra el archivo en disco, no el archivo entero; y con
  LSP, los errores y advertencias del archivo abierto (`"context_diagnostics"`).

## Proceso persistente y permisos

Con `"persistent_chat": true` (el valor de fábrica) cada chat deja un `claude` vivo mientras dura
la conversación, en vez de lanzar uno por mensaje: el segundo mensaje ya no paga el arranque. El
proceso se reemplaza al cambiar de modo o de modelo, y se cierra con el chat, con «Chat nuevo» o
al retomar otra conversación. Si ese proceso no llega a arrancar, el plugin lo anota en el chat y
sigue con un proceso por mensaje hasta reiniciar Sublime.

Ese proceso puede preguntar antes de actuar. En modo edición el chat suma las herramientas de
`"ask_tools"` y en sólo lectura y plan las de `"read_ask_tools"` (de fábrica, `Bash` en ambos:
en lectura sirve para correr pruebas, mirar git o reproducir un error). Cuando Claude quiere
correr un comando —o leer o editar fuera de la carpeta de trabajo— aparece un panel con
**Permitir**, **Permitir siempre** o **Rechazar**; cerrarlo sin elegir es rechazar. «Permitir
siempre» vale para ese programa (`pytest`, `git log`) o ese sitio web, no para la herramienta
entera, y no se ofrece para comandos compuestos ni intérpretes (`python -c`, `bash`, `sudo`). El comando completo queda
escrito en el chat (`? pide permiso: Bash pytest -q`). Lo que ya permitan tus ajustes de Claude
Code no se pregunta. Cuando el permiso es para editar o escribir un archivo, el cambio queda a la vista
en el chat como diff antes de decidir. Lo que haga un comando no entra en el diff ni se puede
revertir desde el plugin; en un repositorio git, el chat toma antes una foto de los archivos
versionados (`"git_checkpoint"`) y, si los comandos cambiaron algo, lo avisa junto con el
`git checkout` que lo devuelve. `"ask_tools": ""` y `"read_ask_tools": ""` dejan el chat sin ejecutar comandos.

## Comandos

Están en la paleta (`Ctrl+Shift+P` → «Claude»), en el menú contextual y en Tools → Claude.
Los atajos son `Ctrl+Alt+C` seguido de una letra.

| Comando | Letra | Qué hace |
|---|---|---|
| Abrir chat | `a` | Abre el chat o lleva el cursor a él. |
| Chat nuevo | `n` | Vacía el chat y olvida la conversación. |
| Conversaciones anteriores… | `h` | Retoma un chat pasado. |
| Compactar la conversación | `x` | Resume lo conversado para liberar contexto; la conversación sigue. |
| Abrir otro chat | `y` | Una conversación más, en su propia pestaña. |
| Elegir modo del chat… | `u` | Sólo lectura, plan o edición. |
| Plegar o desplegar los bloques del chat | `k` | Abre los diffs y el código largo, o los vuelve a plegar. |
| Elegir modelo… | `j` | Predeterminado, Fable, Opus, Sonnet, Haiku o Automático para las próximas consultas. |
| Elegir esfuerzo… | `f` | Cuánto razona antes de responder, de bajo a máximo, o automático; queda anotado en el cierre de cada turno. |
| Revisar errores de este archivo | `r` | Revisa el archivo, o sólo la selección. |
| Revisar cambios sin confirmar (git) | `d` | Revisa `git diff HEAD`. |
| Explicar errores del último build | `b` | Lee la salida del último build de Sublime. |
| Diagnosticar logs del servidor… | `o` | Lista los servicios del servidor por SSH, trae los logs del elegido y los diagnostica. Sólo lee. |
| Escribir código aquí… | `w` | Genera código y lo inserta en el cursor o reemplaza la selección (`Ctrl+Z` lo deshace). |
| Editar este archivo… | `e` | Claude modifica el archivo en disco según una instrucción. |
| Corregir avisos del linter | `l` | Corrige lo que marcan pyright y ruff (vía LSP; si no, ruff suelto). |
| Generar pruebas (pytest) | `p` | Para la selección o el módulo, siguiendo la convención del proyecto. |
| Correr las pruebas y corregir lo que falle | `c` | Corre pytest sobre las pruebas del archivo (`test_<nombre>.py`) con el Python del proyecto; si fallan, Claude corrige el código sin debilitar la prueba. |
| Revertir la última edición | `z` | Devuelve los archivos a como estaban antes de la última edición de Claude; si tocó varios, deja elegir uno o todos. Repetirlo deshace la edición anterior, hasta 10 turnos atrás. |
| Proponer mensaje de commit | `g` | De lo preparado con `git add` (o de todo); queda en el chat y en el portapapeles. No hace el commit. |
| Abrir en terminal | `t` | Claude Code interactivo, en una pestaña de Terminus si está instalado; si hay un archivo guardado abierto, arranca con él. |
| Detener | `s` | Corta la consulta del chat en uso. |

## Ediciones

Los comandos que editan guardan antes el archivo y aplican el cambio en disco. Un hook de Claude
Code (`hooks/snapshot.py`) copia cada archivo justo antes de que Claude lo toque; al terminar, el
chat muestra el diff y «Revertir la última edición» lo deshace, avisando si el archivo cambió
después. Se apaga con `"review_edits": false`.

Si la edición tocó varios archivos, «Revertir la última edición» pregunta cuál: uno solo o
todos. Lo que quede sin revertir sigue disponible, y Claude se entera en el mensaje siguiente.

Cada turno que edita deja su propio respaldo (se guardan los 10 últimos): al revertir uno, el
comando queda apuntando al anterior. Tras una edición, los errores que LSP marque en los
archivos tocados se anotan en el chat y viajan con el mensaje siguiente (`"check_after_edit"`).

El diff llega plegado, un pliegue por archivo: se ve el encabezado de cada uno y el total de
líneas agregadas y quitadas. `Ctrl+Alt+C`, `K` (o la marca del pliegue) lo abre.
`"fold_edits": false` lo deja siempre desplegado.

## Servidor

`remote_host`, `remote_list_command` y `remote_logs_command` (Preferences: Claude Settings) definen
a qué servidor se entra y qué dos comandos se ejecutan; vienen para Docker y traen el ejemplo
para systemd. La conexión usa `BatchMode`: si SSH necesitara pedir una clave, falla en vez de
quedarse esperando.

## Pruebas

`python3 -m unittest discover -s tests`. `test_plugin.py` usa un Sublime simulado y
`LiveSession` un CLI de mentira que habla el protocolo del proceso persistente; la clase
`EndToEnd` de `test_core.py` llama al CLI de verdad y se salta con `CLAUDE_ST_OFFLINE=1`.

En Windows el plugin lanza y corta el proceso de Claude con los medios de ese sistema, pero
«Abrir en terminal» y los comandos del servidor siguen pensados para Linux y macOS.

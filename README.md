# ClaudeCode para Sublime Text

Llama a Claude Code (`claude`) desde el editor. Requiere Sublime Text 4 y el CLI ya autenticado.

## El chat

`Ctrl+Alt+C`, `A` abre el chat en una columna a la derecha. La conversación queda arriba y se
escribe después de la marca `❯`:

- `Enter` envía; `Shift+Enter` agrega una línea.
- El archivo en que estabas trabajando (y su selección) viaja como contexto cuando cambia.
- Todos los comandos responden en el chat, así que se les puede contestar ahí.
- Doble clic sobre una cita `ruta:línea` abre el archivo.
- `@` abre la lista de archivos del proyecto y deja escrito `@ruta`; Claude lee los adjuntos
  antes de responder (`Esc` deja la arroba sola).
- Cada conversación se guarda al terminar un turno; «Conversaciones anteriores…» la devuelve al
  chat y la continúa. Se conservan las 50 más recientes, en `Packages/User/ClaudeCode/history`.
- El chat tiene tres modos, que se eligen con `Ctrl+Alt+C`, `U`: **sólo lectura** (el de
  partida), **plan** (investiga y propone, sin tocar nada; la pestaña se llama «Claude ◇») y
  **edición** («Claude ✎»). `Ctrl+Alt+C`, `M` alterna entre lectura y edición, y los comandos
  que editan la activan solos.
  `"chat_edit_default": true` hace que nazca pudiendo editar. Cada turno termina indicando el
  modo (`— listo (12 s) · sólo lectura`) y Claude sabe en cuál está, así que avisa cómo
  habilitar la edición en vez de limitarse a mostrar el código.
- Mientras Claude piensa, prepara una herramienta o espera, el chat y la barra de estado lo
  dicen, con los segundos que lleva (`⋯ pensando… 12 s`); la línea desaparece cuando llega la
  respuesta. Si trabaja un subagente, muestra qué herramienta usa.
- `Enter` mientras Claude responde (o `Ctrl+Alt+C`, `Q`) deja el mensaje en cola: se envía solo
  al terminar. Si la respuesta se detiene o falla, lo que estaba en cola vuelve al borrador.
- «Abrir otro chat» (`Ctrl+Alt+C`, `Y`) abre una conversación más, que puede correr mientras la
  primera responde. Los comandos responden en el último chat que se usó.
- El cierre de cada turno dice cuánto de la ventana de contexto va usado
  (`— listo (12 s) · sólo lectura · contexto 38 %`); cerca del 100 % conviene un chat nuevo.
  Los avisos de límite de uso del plan se anotan en el chat con la hora en que se restablece.
- Un bloque de código largo de una respuesta llega plegado (`"fold_code_lines"`), igual que los
  diffs.
- Con cambios sin guardar viaja el diff contra el archivo en disco, no el archivo entero; y con
  LSP, los errores y advertencias del archivo abierto (`"context_diagnostics"`).

## Proceso persistente y permisos

Con `"persistent_chat": true` (el valor de fábrica) cada chat deja un `claude` vivo mientras dura
la conversación, en vez de lanzar uno por mensaje: el segundo mensaje ya no paga el arranque. El
proceso se reemplaza al cambiar de modo o de modelo, y se cierra con el chat, con «Chat nuevo» o
al retomar otra conversación. Si ese proceso no llega a arrancar, el plugin lo anota en el chat y
sigue con un proceso por mensaje hasta reiniciar Sublime.

Ese proceso puede preguntar antes de actuar. En modo edición el chat suma las herramientas de
`"ask_tools"` (de fábrica, `Bash`): cuando Claude quiere correr un comando —o leer o editar fuera
de la carpeta de trabajo— aparece un panel con **Permitir**, **Permitir siempre** esa herramienta
en la conversación o **Rechazar**; cerrarlo sin elegir es rechazar. El comando completo queda
escrito en el chat (`? pide permiso: Bash pytest -q`). Lo que ya permitan tus ajustes de Claude
Code no se pregunta. Lo que haga un comando no entra en el diff ni se puede revertir desde el
plugin. `"ask_tools": ""` deja el chat sin ejecutar comandos, como antes.

## Comandos

Están en la paleta (`Ctrl+Shift+P` → «Claude»), en el menú contextual y en Tools → Claude.
Los atajos son `Ctrl+Alt+C` seguido de una letra.

| Comando | Letra | Qué hace |
|---|---|---|
| Abrir chat | `a` | Abre el chat o lleva el cursor a él. |
| Chat nuevo | `n` | Vacía el chat y olvida la conversación. |
| Conversaciones anteriores… | `h` | Retoma un chat pasado. |
| Adjuntar un archivo al mensaje… | `i` | Lo mismo que escribir `@` en el chat. |
| Abrir otro chat | `y` | Una conversación más, en su propia pestaña. |
| Permitir o impedir que el chat edite | `m` | Alterna entre sólo lectura y edición de archivos. |
| Elegir modo del chat… | `u` | Sólo lectura, plan o edición. |
| Dejar un mensaje en cola… | `q` | Se envía cuando Claude termine de responder. |
| Plegar o desplegar los bloques del chat | `k` | Abre los diffs y el código largo, o los vuelve a plegar. |
| Elegir modelo… | `j` | Predeterminado, Opus, Sonnet o Haiku para las próximas consultas. |
| Revisar errores de este archivo | `r` | Revisa el archivo, o sólo la selección. |
| Revisar cambios sin confirmar (git) | `d` | Revisa `git diff HEAD`. |
| Explicar errores del último build | `b` | Lee la salida del último build de Sublime. |
| Explicar el error del portapapeles | `x` | Para un traceback copiado de una terminal. |
| Ver el estado del servidor | `v` | Lista por SSH qué servicios están caídos o reiniciándose. No usa a Claude. |
| Diagnosticar logs del servidor… | `o` | Lista los servicios del servidor por SSH, trae los logs del elegido y los diagnostica. Sólo lee. |
| Escribir código aquí… | `w` | Genera código y lo inserta en el cursor o reemplaza la selección (`Ctrl+Z` lo deshace). |
| Editar este archivo… | `e` | Claude modifica el archivo en disco según una instrucción. |
| Corregir avisos del linter | `l` | Corrige lo que marcan pyright y ruff (vía LSP; si no, ruff suelto). |
| Generar pruebas (pytest) | `p` | Para la selección o el módulo, siguiendo la convención del proyecto. |
| Correr las pruebas y corregir lo que falle | `c` | Corre pytest sobre las pruebas del archivo (`test_<nombre>.py`) con el Python del proyecto; si fallan, Claude corrige el código sin debilitar la prueba. |
| Revertir la última edición | `z` | Devuelve los archivos a como estaban antes de la última edición de Claude; si tocó varios, deja elegir uno o todos. |
| Proponer mensaje de commit | `g` | De lo preparado con `git add` (o de todo); queda en el chat y en el portapapeles. No hace el commit. |
| Abrir en terminal | `t` / `f` | Claude Code interactivo, en una pestaña de Terminus si está instalado (`f`: con el archivo actual). |
| Detener | `s` | Corta la consulta del chat en uso. |

## Ediciones

Los comandos que editan guardan antes el archivo y aplican el cambio en disco. Un hook de Claude
Code (`hooks/snapshot.py`) copia cada archivo justo antes de que Claude lo toque; al terminar, el
chat muestra el diff y «Revertir la última edición» lo deshace, avisando si el archivo cambió
después. Se apaga con `"review_edits": false`.

Si la edición tocó varios archivos, «Revertir la última edición» pregunta cuál: uno solo o
todos. Lo que quede sin revertir sigue disponible, y Claude se entera en el mensaje siguiente.

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

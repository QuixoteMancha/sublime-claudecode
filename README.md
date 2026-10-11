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
- El chat es de sólo lectura hasta que se le permite editar (`Ctrl+Alt+C`, `M`, o al usar un
  comando que edita); la pestaña pasa a llamarse «Claude ✎». Nunca ejecuta comandos.

## Comandos

Están en la paleta (`Ctrl+Shift+P` → «Claude»), en el menú contextual y en Tools → Claude.
Los atajos son `Ctrl+Alt+C` seguido de una letra.

| Comando | Letra | Qué hace |
|---|---|---|
| Abrir chat | `a` | Abre el chat o lleva el cursor a él. |
| Chat nuevo | `n` | Vacía el chat y olvida la conversación. |
| Conversaciones anteriores… | `h` | Retoma un chat pasado. |
| Adjuntar un archivo al mensaje… | `i` | Lo mismo que escribir `@` en el chat. |
| Permitir o impedir que el chat edite | `m` | Alterna entre sólo lectura y edición de archivos. |
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
| Revertir la última edición | `z` | Devuelve los archivos a como estaban antes de la última edición de Claude. |
| Proponer mensaje de commit | `g` | De lo preparado con `git add` (o de todo); queda en el chat y en el portapapeles. No hace el commit. |
| Abrir en terminal | `t` / `f` | Claude Code interactivo, en una pestaña de Terminus si está instalado (`f`: con el archivo actual). |
| Detener | `s` | Corta la consulta en curso. |

## Ediciones

Los comandos que editan guardan antes el archivo y aplican el cambio en disco. Un hook de Claude
Code (`hooks/snapshot.py`) copia cada archivo justo antes de que Claude lo toque; al terminar, el
chat muestra el diff y «Revertir la última edición» lo deshace, avisando si el archivo cambió
después. Se apaga con `"review_edits": false`.

## Servidor

`remote_host`, `remote_list_command` y `remote_logs_command` (Preferences: Claude Settings) definen
a qué servidor se entra y qué dos comandos se ejecutan; vienen para Docker y traen el ejemplo
para systemd. La conexión usa `BatchMode`: si SSH necesitara pedir una clave, falla en vez de
quedarse esperando.

## Pruebas

`python3 -m unittest discover -s tests`. `test_plugin.py` usa un Sublime simulado; la clase
`EndToEnd` de `test_core.py` llama al CLI de verdad y se salta con `CLAUDE_ST_OFFLINE=1`.

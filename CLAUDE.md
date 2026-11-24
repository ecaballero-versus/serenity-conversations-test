# Proyecto: histórico de conversaciones de Serenity

Contexto completo del diseño: @docs/handoff.md

## Reglas

- Responde en español.
- Nunca escribas datos personales de alumnos (emails, contenido de conversaciones) en el código, los
  tests, los commits ni los logs. Usa datos ficticios en ejemplos y tests.
- Las credenciales van en `.env`, que está en `.gitignore`. Nunca las escribas en el código.
- Contra la API de Serenity solo se hacen lecturas. Las escrituras (feedback) solo sobre
  conversaciones de prueba y con confirmación explícita.
- No ejecutes comandos destructivos (borrar archivos, bases de datos o ramas) sin confirmación.
- El entorno es Windows sin permisos de administrador: no propongas soluciones que requieran
  instalar software como administrador.

# FASE 4: comando migrar_a_grabados

Responde en español, con resúmenes breves y sin tablas largas.

Crea el management command `migrar_a_grabados`, que pasa los datos de `OrdenFabricacion` (sistema viejo) a los modelos nuevos: `Grabado`, `FabricacionGrabado`, `EnvioMaquina`, `Maquina` y `LegadoOrden`. Lee primero `CLAUDE.md`, `models.py`, `services/grabados.py` y el comando `auditar_grabados`.

## SEGURIDAD

- Por defecto corre en modo simulación (dry-run): no guarda nada. Solo guarda si se pasa `--aplicar`.
- Todo en una sola transacción. Al final comprueba que `LegadoOrden.count()` sea igual a `OrdenFabricacion.count()`; si no cuadra, revierte todo.
- Idempotente: se salta las filas que ya tienen `LegadoOrden`.
- `OrdenFabricacion` no se modifica nunca.
- Si la API externa no responde, se detiene sin guardar nada.

## REGLAS

1. Agrupación: por (OF Referencia, o la propia OF si no tiene, proceso). La referencia se toma literal, sin seguir cadenas.
2. Si ya existe un `Grabado` con esa OF de origen y proceso (creado desde Alta de Grabado), se reutiliza; no se duplica. Si su estado choca con el de las filas legadas, se reporta como conflicto.
3. Estado del grabado según la fila más reciente que no sea PENDIENTE: COMPLETADO pasa a APROBADO; REPETIR o REVISION, a REPETIR; EN_MAQUINA, a EN_MAQUINA con un `EnvioMaquina` abierto; EN_PROCESO, a APROBADO. Todos los migrados llevan `aprobado_legado=True` y ningún K1.
4. Filas PENDIENTE: no se migran. Quedan en `LegadoOrden` con rol SIN_MIGRAR y no cuentan para el estado.
5. Fabricaciones: la primera fila con datos técnicos (por fecha de creación) es la INICIAL. Una fila posterior es REPETICION si antes en el mismo grupo hubo una fila en REPETIR o REVISION. El resto son USO (solo en `LegadoOrden`).
6. Envíos: las filas COMPLETADO, REPETIR o REVISION se convierten en `EnvioMaquina` cerrados, con `recogido_el` igual a `actualizado_el`. Las EN_MAQUINA, en envíos abiertos.
7. Máquinas: se buscan o crean en el catálogo con `normalizar_nombre_maquina` (mayúsculas y espacios). Los envíos sin máquina usan una máquina "SIN MÁQUINA (LEGADO)" inactiva.
8. Cliente, referencia y sobre: de la API para la OF de origen, consultando en lote. Si no aparece, se usa el valor de la fila legada (saltando los que empiezan con "FALLO:" o "Físico:") y se marca `datos_manuales=True`.
9. `usos_acumulados`: la suma del grupo. El usuario de la fila pasa a `registrado_por`.
10. Las descripciones que empiezan con "FALLO:" o "Físico:" pasan al comentario del envío correspondiente.
11. No se copian fotos. `tiempo` y `compensacion` se copian tal cual como texto.
12. Pesos de filas anteriores al 2026-08-12: se copian tal cual, sin convertir, y se marcan para revisión.

## REPORTE

En pantalla y también guardado en un archivo de texto con fecha y hora:

- Filas PENDIENTE, por origen y según tengan datos técnicos.
- Cadenas de referencias encontradas.
- Filas detectadas como REPETICION.
- Nombres de máquina con su conteo, agrupados por forma normalizada para detectar duplicados.
- OF que la API no encuentra.
- Filas con pesos anteriores al 2026-08-12.
- Conflictos con grabados creados desde Alta.
- Conteos finales por rol y por estado, y la verificación de la conciliación.

## PRUEBAS Y EJECUCIÓN

- Agrega pruebas del comando, incluida una que confirme que sin `--aplicar` no se guarda nada y que la conciliación revierte si no cuadra.
- Corre `manage.py test` y luego el comando en modo simulación sobre la base de desarrollo, y muestra el reporte.

## RESTRICCIONES

- No corras el comando con `--aplicar`.
- No hagas git add, commit ni push.

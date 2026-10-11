"""
Un Sobre por cada grabado existente y su enlace (Grabado.sobre_asociado).

- Código: el texto de Grabado.sobre limpio (sin espacios sobrantes; "10567.0"
  -> "10567"). Si está vacío, el sobre se crea igual con el código vacío.
- Ubicación vacía, estado ACTIVO, sin creado_por.
- Segura de repetir: solo mira grabados sin sobre y reutiliza el Sobre de su
  OF + proceso si ya existe (además lo impide sobre_unico_of_proceso).
- No cambia ningún dato del grabado: el enlace se hace con QuerySet.update(),
  que no toca actualizado_el (lo usa el Inventario para ordenar).
"""
import re

from django.db import migrations

PATRON_DECIMAL_CERO = re.compile(r'^(\d+)\.0+$')
# SQL Server admite como mucho 2100 parámetros por consulta.
LOTE = 150


def limpiar_codigo(valor):
    # Copia congelada de services.sobres.limpiar_codigo_sobre.
    codigo = ' '.join(str(valor).split()) if valor is not None else ''
    coincide = PATRON_DECIMAL_CERO.match(codigo)
    return coincide.group(1) if coincide else codigo


def crear_sobres(apps, schema_editor):
    Grabado = apps.get_model('gestion_grabados', 'Grabado')
    Sobre = apps.get_model('gestion_grabados', 'Sobre')

    pendientes = list(Grabado.objects.filter(sobre_asociado__isnull=True)
                      .order_by('id').values('id', 'of_origen', 'proceso', 'sobre'))
    if not pendientes:
        return

    existentes = {(s['of_origen'], s['proceso']) for s in Sobre.objects.values('of_origen', 'proceso')}
    nuevos = [Sobre(of_origen=g['of_origen'], proceso=g['proceso'], codigo=limpiar_codigo(g['sobre']),
                    ubicacion=None, estado='ACTIVO')
              for g in pendientes if (g['of_origen'], g['proceso']) not in existentes]
    Sobre.objects.bulk_create(nuevos, batch_size=LOTE)

    ids = {(s['of_origen'], s['proceso']): s['id']
           for s in Sobre.objects.values('id', 'of_origen', 'proceso')}
    for g in pendientes:
        Grabado.objects.filter(pk=g['id']).update(sobre_asociado_id=ids[(g['of_origen'], g['proceso'])])


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_grabados', '0025_sobre'),
    ]

    operations = [
        # Al revertir no hace nada: deshacer 0025 borra la tabla de sobres.
        migrations.RunPython(crear_sobres, migrations.RunPython.noop),
    ]

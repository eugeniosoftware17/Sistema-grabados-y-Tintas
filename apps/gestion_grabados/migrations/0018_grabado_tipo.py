"""
Grabado.tipo (K1 / DIRECTO / LEGADO) reemplaza a Grabado.aprobado_legado:
1. se agrega `tipo` con default K1;
2. los grabados con aprobado_legado=True pasan a LEGADO (y a la inversa al revertir);
3. se quita `aprobado_legado`.
"""
from django.db import migrations, models


def legado_a_tipo(apps, schema_editor):
    Grabado = apps.get_model('gestion_grabados', 'Grabado')
    Grabado.objects.filter(aprobado_legado=True).update(tipo='LEGADO')


def tipo_a_legado(apps, schema_editor):
    Grabado = apps.get_model('gestion_grabados', 'Grabado')
    Grabado.objects.filter(tipo='LEGADO').update(aprobado_legado=True)


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_grabados', '0017_fabricaciongrabado_revisar_pesos'),
    ]

    operations = [
        migrations.AddField(
            model_name='grabado',
            name='tipo',
            field=models.CharField(
                choices=[('K1', 'K1 – de prueba'), ('DIRECTO', 'Producción – directo'),
                         ('LEGADO', 'Legado (migrado)')],
                default='K1',
                help_text='Cómo se creó: K1 (prueba, requiere aprobación), Producción directa '
                          '(aprobado al guardar, sin K1) o Legado (migrado desde OrdenFabricacion).',
                max_length=10,
                verbose_name='Tipo',
            ),
        ),
        migrations.RunPython(legado_a_tipo, tipo_a_legado),
        migrations.RemoveField(
            model_name='grabado',
            name='aprobado_legado',
        ),
    ]

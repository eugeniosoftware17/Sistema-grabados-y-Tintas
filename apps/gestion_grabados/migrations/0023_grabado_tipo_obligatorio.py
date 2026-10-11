"""
Grabado.tipo sin default: quien crea un grabado tiene que indicar K1, DIRECTO
o LEGADO. Sin default Django guardaría '' en silencio, así que se agrega una
restricción que solo admite esos tres valores (un create() sin tipo falla).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_grabados', '0022_grabado_inactivo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='grabado',
            name='tipo',
            field=models.CharField(
                choices=[('K1', 'K1 – de prueba'), ('DIRECTO', 'Producción – directo'),
                         ('LEGADO', 'Legado (migrado)')],
                help_text='Cómo se creó: K1 (prueba, requiere aprobación), Producción directa '
                          '(aprobado al guardar, sin K1) o Legado (migrado desde OrdenFabricacion).',
                max_length=10,
                verbose_name='Tipo',
            ),
        ),
        migrations.AddConstraint(
            model_name='grabado',
            constraint=models.CheckConstraint(condition=models.Q(tipo__in=['K1', 'DIRECTO', 'LEGADO']),
                                              name='grabado_tipo_valido'),
        ),
    ]

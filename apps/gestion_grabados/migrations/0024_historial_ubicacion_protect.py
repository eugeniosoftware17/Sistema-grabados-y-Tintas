"""HistorialUbicacion.grabado: CASCADE -> PROTECT (el historial no se borra con el grabado)."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_grabados', '0023_grabado_tipo_obligatorio'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historialubicacion',
            name='grabado',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='historial_ubicaciones', to='gestion_grabados.grabado'),
        ),
    ]

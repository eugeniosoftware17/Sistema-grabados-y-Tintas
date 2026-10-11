"""
Grabado INACTIVO: un grabado nunca se elimina, se desactiva.
1. estado admite INACTIVO;
2. campos de la última desactivación (quién, cuándo, motivo y estado previo);
3. HistorialActivacion: una fila por cada desactivación / reactivación.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

ESTADOS = [('EN_FABRICACION', 'En Fabricación'), ('PENDIENTE_K1', 'Pendiente de K1'),
           ('APROBADO', 'Aprobado'), ('EN_MAQUINA', 'En Máquina'), ('REPETIR', 'Para Repetir'),
           ('INACTIVO', 'Inactivo')]


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_grabados', '0021_historial_ubicacion_origen_consulta'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='grabado',
            name='estado',
            field=models.CharField(choices=ESTADOS, default='EN_FABRICACION', max_length=20,
                                   verbose_name='Estado'),
        ),
        migrations.AddField(
            model_name='grabado',
            name='desactivado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='+', to=settings.AUTH_USER_MODEL,
                                    verbose_name='Desactivado por'),
        ),
        migrations.AddField(
            model_name='grabado',
            name='desactivado_el',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Desactivado el'),
        ),
        migrations.AddField(
            model_name='grabado',
            name='motivo_desactivacion',
            field=models.TextField(blank=True, default='', verbose_name='Motivo de desactivación'),
        ),
        migrations.AddField(
            model_name='grabado',
            name='estado_previo',
            field=models.CharField(blank=True, choices=ESTADOS, default='', max_length=20,
                                   verbose_name='Estado previo a la desactivación'),
        ),
        migrations.CreateModel(
            name='HistorialActivacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accion', models.CharField(choices=[('DESACTIVAR', 'Desactivado'), ('REACTIVAR', 'Reactivado')],
                                            max_length=20, verbose_name='Acción')),
                ('motivo', models.TextField(blank=True, default='', verbose_name='Motivo')),
                ('estado', models.CharField(choices=ESTADOS, max_length=20, verbose_name='Estado')),
                ('fecha', models.DateTimeField(auto_now_add=True, verbose_name='Fecha')),
                ('grabado', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='historial_activaciones',
                                              to='gestion_grabados.grabado')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name='+', to=settings.AUTH_USER_MODEL,
                                              verbose_name='Usuario')),
            ],
            options={
                'verbose_name': 'Activación / desactivación',
                'verbose_name_plural': 'Historial de activaciones',
                'ordering': ['-fecha', '-id'],
            },
        ),
    ]

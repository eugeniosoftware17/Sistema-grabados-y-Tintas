from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_grabados', '0016_quitar_foto_dano_envio'),
    ]

    operations = [
        migrations.AddField(
            model_name='fabricaciongrabado',
            name='revisar_pesos',
            field=models.BooleanField(
                default=False,
                help_text='Migrada de una fila anterior al 2026-08-12: los pesos se copiaron tal cual '
                          'y pueden estar en kg en vez de gramos.',
                verbose_name='Revisar pesos',
            ),
        ),
    ]

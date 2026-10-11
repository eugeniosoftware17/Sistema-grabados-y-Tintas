from django.contrib.auth.management import create_permissions
from django.db import migrations

GRUPO_SUPERVISORES = 'Supervisores de Producción'
CODENAME_DECIDIR_K1 = 'decidir_pruebak1'


def borrar_supervisores(apps, schema_editor):
    """El K1 ya no depende de un supervisor: lo decide cualquier usuario con
    acceso al módulo Grabados. Borra el grupo "Supervisores de Producción" y el
    permiso decidir_pruebak1 (Django no borra solo los permisos que se quitan de
    Meta.permissions). Corre después de core/0002, que ya pasó a sus usuarios
    al grupo "Grabados"."""
    db_alias = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    Group.objects.using(db_alias).filter(name=GRUPO_SUPERVISORES).delete()
    Permission.objects.using(db_alias).filter(
        codename=CODENAME_DECIDIR_K1, content_type__app_label='gestion_grabados',
    ).delete()


def recrear_supervisores(apps, schema_editor):
    """Reversa: vuelve a crear el permiso y el grupo (vacío; los usuarios no se
    recuperan)."""
    db_alias = schema_editor.connection.alias
    app_config = apps.get_app_config('gestion_grabados')
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0, using=db_alias)
    app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    permiso = Permission.objects.using(db_alias).get(
        codename=CODENAME_DECIDIR_K1,
        content_type__app_label='gestion_grabados',
        content_type__model='pruebak1',
    )
    grupo, _ = Group.objects.using(db_alias).get_or_create(name=GRUPO_SUPERVISORES)
    grupo.permissions.add(permiso)


class Migration(migrations.Migration):

    dependencies = [
        ('gestion_grabados', '0018_grabado_tipo'),
        ('core', '0002_grupo_grabados'),
    ]

    # RunPython va antes que AlterModelOptions: al revertir, el estado ya vuelve
    # a tener decidir_pruebak1 en Meta cuando recrear_supervisores lo necesita.
    operations = [
        migrations.RunPython(borrar_supervisores, recrear_supervisores),
        migrations.AlterModelOptions(
            name='pruebak1',
            options={'ordering': ['grabado', 'intento'], 'verbose_name': 'Prueba K1',
                     'verbose_name_plural': 'Pruebas K1'},
        ),
    ]

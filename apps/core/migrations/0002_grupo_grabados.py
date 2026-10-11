from django.contrib.auth.management import create_permissions
from django.db import migrations

GRUPO_GRABADOS = 'Grabados'
CODENAME_ACCESO_GRABADOS = 'acceso_grabados'
GRUPO_SUPERVISORES = 'Supervisores de Producción'


def crear_grupo_grabados(apps, schema_editor):
    """Crea el grupo "Grabados" con el permiso de acceso al módulo y mete en él
    a todos los usuarios activos que ya existen, más los del grupo viejo
    "Supervisores de Producción" (aunque estén inactivos), para que nadie pierda
    el acceso que tenía al desplegar. Los usuarios nuevos se agregan a mano desde
    /crdadmin/ (Usuarios → Grupos)."""
    db_alias = schema_editor.connection.alias

    # Los permisos de Meta.permissions se crean en la señal post_migrate, que corre
    # al final de `migrate`; acá se fuerzan para poder asignarlos ya.
    app_config = apps.get_app_config('core')
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0, using=db_alias)
    app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    User = apps.get_model('auth', 'User')

    permiso = Permission.objects.using(db_alias).get(
        codename=CODENAME_ACCESO_GRABADOS,
        content_type__app_label='core',
        content_type__model='accesomodulo',
    )
    grupo, _ = Group.objects.using(db_alias).get_or_create(name=GRUPO_GRABADOS)
    grupo.permissions.add(permiso)

    usuarios = set(User.objects.using(db_alias).filter(is_active=True).values_list('pk', flat=True))
    usuarios |= set(User.objects.using(db_alias)
                    .filter(groups__name=GRUPO_SUPERVISORES).values_list('pk', flat=True))
    grupo.user_set.add(*usuarios)


def quitar_grupo_grabados(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.using(schema_editor.connection.alias).filter(name=GRUPO_GRABADOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0001_acceso_modulo'),
        # El grupo "Supervisores de Producción" (cuyos usuarios se migran acá) lo crea 0015.
        ('gestion_grabados', '0015_grupo_supervisores_produccion'),
    ]

    operations = [
        migrations.RunPython(crear_grupo_grabados, quitar_grupo_grabados),
    ]

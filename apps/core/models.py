from django.db import models


class AccesoModulo(models.Model):
    """
    Modelo sin tabla (managed=False): solo existe para colgar de él los
    permisos de acceso por área del EIS. Cada permiso abre un módulo completo
    (menú + vistas + APIs); ver apps/core/permisos.py.

    Para agregar un área nueva (tintas, mantenimiento, almacén...):
    1. sumar la tupla en Meta.permissions y la entrada en permisos.MODULOS;
    2. `makemigrations core` (solo cambia las opciones del modelo, no la base)
       y una migración de datos si el área necesita su grupo.
    """

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = 'acceso a módulo'
        verbose_name_plural = 'accesos a módulos'
        permissions = [
            ('acceso_grabados', 'Acceso al módulo Grabados'),
        ]

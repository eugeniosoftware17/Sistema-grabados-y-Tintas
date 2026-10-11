"""
Permisos de acceso por área (módulo) del EIS.

Cada módulo tiene un permiso de Django en core.AccesoModulo y un grupo con el
mismo nombre que se asigna a los usuarios desde /crdadmin/ (Usuarios → Grupos).
Los superusuarios tienen todos los permisos sin asignarles nada.

- `requiere_modulo('grabados')`: decorador para una vista.
- `proteger_urls(urlpatterns, 'grabados')`: lo aplica a todas las rutas de una
  app de una sola vez, para que ninguna vista ni API quede sin verificar.
- En plantillas: `{% if perms.core.acceso_grabados %}`.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

MODULOS = {
    'grabados': {'permiso': 'core.acceso_grabados', 'grupo': 'Grabados'},
}


def tiene_modulo(usuario, modulo):
    return usuario.is_authenticated and usuario.has_perm(MODULOS[modulo]['permiso'])


def _es_api(request):
    return '/api/' in request.path


def requiere_modulo(modulo):
    """Sin sesión → login (como @login_required). Con sesión pero sin el
    permiso del módulo → 403: JSON en las rutas /api/, y la plantilla 403.html
    en las páginas."""
    if modulo not in MODULOS:
        raise ValueError(f'Módulo desconocido: {modulo}')

    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not tiene_modulo(request.user, modulo):
                if _es_api(request):
                    return JsonResponse(
                        {'status': 'error', 'message': 'No tienes acceso a este módulo.'}, status=403)
                raise PermissionDenied('No tienes acceso a este módulo.')
            return vista(request, *args, **kwargs)
        return login_required(envoltura)
    return decorador


def proteger_urls(urlpatterns, modulo):
    """Envuelve con requiere_modulo la vista de cada ruta de la lista."""
    proteger = requiere_modulo(modulo)
    for patron in urlpatterns:
        patron.callback = proteger(patron.callback)
    return urlpatterns

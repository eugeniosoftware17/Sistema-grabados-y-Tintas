"""
Pantalla Sobres (/grabados/sobres/): lista de sobres con búsqueda en el
servidor, ubicación editable y desactivar / reactivar.

Las vistas solo parsean el request; las reglas viven en services/sobres.py.
El permiso del módulo Grabados lo verifica urls.py (proteger_urls).
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from . import selectors
from .services import sobres as servicio
from .services.grabados import ErrorGrabado
from .views_grabados import _error, _leer_json


@login_required
def sobres(request):
    return render(request, 'sobres.html')


@login_required
def api_sobres(request):
    """Sobres que coinciden con ?q= (OF, código o ubicación) y ?filtros=<JSON> (filtros
    por columna, ver selectors.CAMPOS_SOBRES), hasta 500. Solo lectura."""
    if request.method != 'GET':
        return _error('Método no permitido', status=405)
    try:
        filtros = json.loads(request.GET.get('filtros') or '[]')
        resultado = selectors.lista_sobres(q=request.GET.get('q', '').strip(), filtros=filtros)
    except json.JSONDecodeError:
        return _error('Filtros inválidos.')
    except selectors.FiltroConsultaInvalido as e:
        return _error(str(e))
    return JsonResponse({'status': 'ok', **resultado})


@login_required
def api_sobres_valores(request):
    """Valores distintos de una columna de Sobres (todos los sobres) para su filtro."""
    try:
        resultado = selectors.valores_columna_sobres(request.GET.get('col', ''), request.GET.get('q', '').strip())
    except selectors.FiltroConsultaInvalido as e:
        return _error(str(e))
    return JsonResponse({'status': 'ok', **resultado})


@login_required
def api_sobre_ubicacion(request, sobre_id):
    """Cambia la ubicación de un sobre. JSON: {ubicacion}. El servicio valida el
    formato y deja el cambio en HistorialUbicacionSobre."""
    if request.method != 'POST':
        return _error('Método no permitido', status=405)
    data = _leer_json(request)
    if data is None:
        return _error('Datos inválidos.')
    try:
        sobre = servicio.cambiar_ubicacion_sobre(sobre_id=sobre_id, ubicacion=data.get('ubicacion'),
                                                 usuario=request.user)
    except ErrorGrabado as e:
        return _error(e.mensaje, status=e.status)
    mensaje = (f'Ubicación del sobre de {sobre.of_origen} {sobre.proceso}: {sobre.ubicacion}.'
               if sobre.ubicacion_cambio else 'La ubicación no cambió.')
    return JsonResponse({
        'status': 'ok',
        'message': mensaje,
        'cambio': sobre.ubicacion_cambio,
        'ubicacion': sobre.ubicacion,
        'actualizado_el': selectors.formatear_fecha(sobre.actualizado_el),
    })


def _cambiar_activacion(request, sobre_id, desactivar):
    if request.method != 'POST':
        return _error('Método no permitido', status=405)
    data = _leer_json(request)
    if data is None:
        return _error('Datos inválidos.')
    try:
        if desactivar:
            sobre = servicio.desactivar_sobre(sobre_id=sobre_id, motivo=data.get('motivo'), usuario=request.user)
            mensaje = f'Sobre de {sobre.of_origen} {sobre.proceso} desactivado.'
        else:
            sobre = servicio.reactivar_sobre(sobre_id=sobre_id, usuario=request.user)
            mensaje = f'Sobre de {sobre.of_origen} {sobre.proceso} reactivado.'
    except ErrorGrabado as e:
        return _error(e.mensaje, status=e.status)
    return JsonResponse({'status': 'ok', 'message': mensaje, 'estado': sobre.estado})


@login_required
def api_sobre_desactivar(request, sobre_id):
    """Desactiva un sobre (nunca se elimina). JSON: {motivo}."""
    return _cambiar_activacion(request, sobre_id, desactivar=True)


@login_required
def api_sobre_reactivar(request, sobre_id):
    """Reactiva un sobre inactivo."""
    return _cambiar_activacion(request, sobre_id, desactivar=False)

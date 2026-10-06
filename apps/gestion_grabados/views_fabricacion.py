"""
RETIRADA (fase 3): la pantalla Fabricación (alta manual de grabados de stock en
OrdenFabricacion) la reemplaza Alta de Grabado (views_grabados.py).

- La página redirige a Alta de Grabado y ya no aparece en el menú.
- Los endpoints que escribían (registrar, registrar-lote, editar-ubicación)
  responden 410 sin tocar la base.
- Los de solo lectura se mantienen mientras exista el módulo.

Para borrarlo del todo: este archivo, `templates/fabricacion.html`,
`static/js/fabricacion.js` y sus rutas en `urls.py`.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect

from .models import OrdenFabricacion
from .views import buscar_datos_externos

PROCESOS_VALIDOS = ('STAMPING', 'EMBOSSING')

MENSAJE_RETIRADA = ('La pantalla Fabricación se retiró y ya no registra grabados: '
                    'usa Alta de Grabado.')


def _retirada():
    return JsonResponse({'status': 'error', 'message': MENSAJE_RETIRADA}, status=410)


@login_required
def fabricacion(request):
    return redirect('grabados:alta_grabado')


@login_required
def api_buscar_externo(request):
    """Busca en la base externa CigarRings2012 lo que se pueda autocompletar
    (máquina, sobre, OF referencia, papel, horas previstas) a partir de un OF
    y proceso ingresados a mano. Solo lectura."""
    of_numero = request.GET.get('of', '').strip()
    proceso = request.GET.get('proceso', '').strip().upper()

    if not of_numero or proceso not in PROCESOS_VALIDOS:
        return JsonResponse({'status': 'error', 'message': 'Faltan OF o proceso'}, status=400)

    info = buscar_datos_externos(of_numero, proceso)
    if OrdenFabricacion.objects.filter(of=of_numero.upper(), proceso=proceso).exists():
        info['status_db'] = 'existente'
    return JsonResponse({'status': 'ok', 'data': info})


@login_required
def api_registrar_manual(request):
    return _retirada()


@login_required
def api_listar_manual(request):
    """Grabados dados de alta desde la pantalla retirada (origen_manual=True). Solo lectura."""
    registros = list(
        OrdenFabricacion.objects.filter(origen_manual=True)
        .order_by('-creado_el')
        .values('of', 'proceso', 'cliente', 'descripcion', 'maquina', 'sobre',
                 'referencia', 'papel', 'ubicacion', 'estado')
    )
    return JsonResponse({'status': 'ok', 'data': registros})


@login_required
def api_registrar_lote(request):
    return _retirada()


@login_required
def api_editar_ubicacion(request):
    return _retirada()

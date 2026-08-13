"""
Alta manual de grabados que ya existen en stock pero no pasaron por el flujo
normal de Plani (no tienen OF de programación en el Excel).

Módulo TEMPORAL: si en algún momento deja de hacer falta el alta manual,
se puede borrar este archivo junto con `templates/fabricacion.html`,
`static/js/fabricacion.js` y las 3 rutas que lo referencian en `urls.py`,
sin tocar el resto de la app (usa `buscar_datos_externos` de `views.py`
como única dependencia compartida).
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import OrdenFabricacion
from .views import buscar_datos_externos

PROCESOS_VALIDOS = ('STAMPING', 'EMBOSSING')
ESTADOS_VALIDOS = dict(OrdenFabricacion.ESTADO_CHOICES)


@login_required
def fabricacion(request):
    return render(request, 'fabricacion.html')


@login_required
def api_buscar_externo(request):
    """Busca en la base externa CigarRings2012 lo que se pueda autocompletar
    (máquina, sobre, OF referencia, papel, horas previstas) a partir de un OF
    y proceso ingresados a mano."""
    of_numero = request.GET.get('of', '').strip()
    proceso = request.GET.get('proceso', '').strip().upper()

    if not of_numero or proceso not in PROCESOS_VALIDOS:
        return JsonResponse({'status': 'error', 'message': 'Faltan OF o proceso'}, status=400)

    info = buscar_datos_externos(of_numero, proceso)
    return JsonResponse({'status': 'ok', 'data': info})


@login_required
def api_registrar_manual(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        of_num = str(data.get('of', '')).strip().upper()
        proceso = str(data.get('proceso', '')).strip().upper()

        if not of_num or proceso not in PROCESOS_VALIDOS:
            return JsonResponse({'status': 'error', 'message': 'OF y proceso son obligatorios'}, status=400)

        horas = data.get('horas_proceso')
        try:
            horas = float(horas) if horas not in (None, '') else None
        except (TypeError, ValueError):
            horas = None

        estado = data.get('estado')
        if estado not in ESTADOS_VALIDOS:
            estado = 'COMPLETADO'

        obj, created = OrdenFabricacion.objects.update_or_create(
            of=of_num, proceso=proceso,
            defaults={
                'cliente': data.get('cliente') or 'Desconocido',
                'descripcion': data.get('descripcion') or '—',
                'referencia': data.get('referencia') or None,
                'maquina': data.get('maquina') or None,
                'sobre': data.get('sobre') or None,
                'papel': data.get('papel') or None,
                'horas_proceso': horas,
                'ubicacion': data.get('ubicacion') or None,
                'estado': estado,
                'fecha_registro': timezone.now().date(),
                'usuario': request.user,
                'origen_manual': True,
                'responsables': f"M-{request.user.username}",
            }
        )
        return JsonResponse({
            'status': 'ok',
            'message': 'Grabado registrado.' if created else 'Grabado actualizado.',
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def api_listar_manual(request):
    """Devuelve los grabados dados de alta desde esta pantalla (origen_manual=True),
    para que la tabla de la página siga mostrándolos aunque se recargue o se
    vuelva a entrar más tarde."""
    registros = list(
        OrdenFabricacion.objects.filter(origen_manual=True)
        .order_by('-creado_el')
        .values('of', 'proceso', 'cliente', 'descripcion', 'maquina', 'sobre',
                 'referencia', 'papel', 'ubicacion', 'estado')
    )
    return JsonResponse({'status': 'ok', 'data': registros})

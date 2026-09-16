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
from .views import buscar_datos_externos, buscar_datos_externos_batch, _normalizar_of, VACIO_INFO_EXTERNA

PROCESOS_VALIDOS = ('STAMPING', 'EMBOSSING')
ESTADOS_VALIDOS = dict(OrdenFabricacion.ESTADO_CHOICES)
ESTADOS_LOTE_VALIDOS = ('PENDIENTE', 'COMPLETADO')


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

    # Misma normalización que usa api_registrar_manual al guardar, para poder
    # avisar si esta OF+proceso ya está cargada en el sistema (evita pisarla sin querer).
    if OrdenFabricacion.objects.filter(of=of_numero.upper(), proceso=proceso).exists():
        info['status_db'] = 'existente'

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


@login_required
def api_registrar_lote(request):
    """Alta de varias OF a la vez para el mismo proceso/estado/ubicación.
    A diferencia de api_registrar_manual, si una OF+proceso ya existe se
    omite (no la pisa)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        proceso = str(data.get('proceso', '')).strip().upper()
        estado = str(data.get('estado', '')).strip().upper()
        ubicacion = (data.get('ubicacion') or '').strip() or None

        if proceso not in PROCESOS_VALIDOS:
            return JsonResponse({'status': 'error', 'message': 'Proceso inválido'}, status=400)
        if estado not in ESTADOS_LOTE_VALIDOS:
            return JsonResponse({'status': 'error', 'message': 'Estado inválido'}, status=400)

        ofs = list(dict.fromkeys(
            of.strip().upper() for of in str(data.get('ofs', '')).split(',') if of.strip()
        ))
        if not ofs:
            return JsonResponse({'status': 'error', 'message': 'Ingresá al menos una OF'}, status=400)

        info_externa = buscar_datos_externos_batch(ofs, proceso)

        detalle = []
        for of_num in ofs:
            existente = OrdenFabricacion.objects.filter(of=of_num, proceso=proceso).first()
            if existente:
                detalle.append({
                    'of': of_num, 'status': 'omitido', 'motivo': 'Ya existe esa OF + proceso',
                    'ubicacion': existente.ubicacion or '',
                })
                continue

            info = info_externa.get(_normalizar_of(of_num), dict(VACIO_INFO_EXTERNA))
            OrdenFabricacion.objects.create(
                of=of_num, proceso=proceso,
                cliente=info['cliente_ext'] if info['encontrado_ext'] else 'Desconocido',
                descripcion=info['descripcion_ext'] if info['encontrado_ext'] else '—',
                referencia=info['ref_ext'] if info['encontrado_ext'] and info['ref_ext'] != '—' else None,
                sobre=info['sobre_ext'] if info['encontrado_ext'] and info['sobre_ext'] != '—' else None,
                ubicacion=ubicacion,
                estado=estado,
                fecha_registro=timezone.now().date(),
                usuario=request.user,
                origen_manual=True,
                responsables=f"M-{request.user.username}",
            )
            detalle.append({'of': of_num, 'status': 'registrado', 'ubicacion': ubicacion or ''})

        registrados = sum(1 for d in detalle if d['status'] == 'registrado')
        return JsonResponse({
            'status': 'ok',
            'registrados': registrados,
            'omitidos': len(detalle) - registrados,
            'detalle': detalle,
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def api_editar_ubicacion(request):
    """Edita solo la ubicación física de una OF+proceso ya existente. Se usa
    desde el resumen del Registro en Lote para corregir la ubicación de OF
    que se omitieron por ya estar cargadas."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        of_num = str(data.get('of', '')).strip().upper()
        proceso = str(data.get('proceso', '')).strip().upper()
        ubicacion = (data.get('ubicacion') or '').strip() or None

        if not of_num or proceso not in PROCESOS_VALIDOS:
            return JsonResponse({'status': 'error', 'message': 'OF y proceso son obligatorios'}, status=400)

        actualizados = OrdenFabricacion.objects.filter(of=of_num, proceso=proceso).update(ubicacion=ubicacion)
        if not actualizados:
            return JsonResponse({'status': 'error', 'message': 'No existe esa OF + proceso'}, status=404)

        return JsonResponse({'status': 'ok', 'ubicacion': ubicacion or ''})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

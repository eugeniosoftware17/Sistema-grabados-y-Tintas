"""
Pantallas de la reestructuración (fase 2), sobre los modelos nuevos:
- Crear Grabado: registra la fabricación de un grabado nuevo (K1 de prueba o
  de producción directa), la refabricación tras un K1 rechazado o la
  refabricación por REPETIR.
- K1 pendientes: los supervisores aprueban o rechazan las pruebas K1.

Las vistas solo parsean el request y devuelven JsonResponse; las reglas de
negocio viven en services/grabados.py.
"""
import json
from functools import wraps

from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse
from django.shortcuts import render

from . import selectors
from .models import Grabado, Maquina, PruebaK1
from .services import grabados as servicio
from .views import _normalizar_of, buscar_datos_externos

PERMISO_DECIDIR_K1 = servicio.PERMISO_DECIDIR_K1


def _error(mensaje, status=400):
    return JsonResponse({'status': 'error', 'message': mensaje}, status=status)


def _leer_json(request):
    try:
        return json.loads(request.body or '{}')
    except (ValueError, UnicodeDecodeError):
        return None


def _of_normalizada(valor):
    of_int = _normalizar_of(valor) if valor not in (None, '') else None
    return str(of_int) if of_int is not None else None


# ============================================================
# CREAR GRABADO (URL /grabados/alta/)
# ============================================================

@login_required
def alta_grabado(request):
    maquinas = Maquina.objects.filter(activa=True).order_by('nombre')
    return render(request, 'alta_grabado.html', {'maquinas': maquinas})


@login_required
def api_alta_buscar(request):
    """Datos externos de la OF y, para cada proceso, qué acción permite la
    pantalla (alta nueva, refabricación, o bloqueado con el motivo)."""
    of_origen = _of_normalizada(request.GET.get('of', '').strip())
    if not of_origen:
        return _error('Ingresa un número de OF válido.')

    info = buscar_datos_externos(of_origen, 'STAMPING')
    # 'encontrado' = la OF tiene fila en el sistema externo; 'completo' = además
    # trae cliente y referencia (puede existir la fila con esos campos en NULL).
    externo = servicio.datos_externos_utiles(info)
    externo.update(
        encontrado=bool(info['encontrado_ext']),
        completo=bool(externo['cliente'] and externo['referencia']),
        proceso=info.get('proceso_ext'),
    )
    procesos = {
        proceso: servicio.evaluar_alta(of_origen, proceso, info, _normalizar_of)
        for proceso in servicio.PROCESOS_VALIDOS
    }
    return JsonResponse({
        'status': 'ok',
        'of': of_origen,
        'externo': externo,
        'procesos': procesos,
        'hay_maquinas': Maquina.objects.filter(activa=True).exists(),
    })


@login_required
def api_alta_registrar(request):
    if request.method != 'POST':
        return _error('Método no permitido', status=405)
    data = _leer_json(request)
    if data is None:
        return _error('Datos inválidos.')

    of_origen = _of_normalizada(str(data.get('of', '')).strip())
    proceso = str(data.get('proceso', '')).strip().upper()
    if not of_origen:
        return _error('Ingresa un número de OF válido.')
    if proceso not in servicio.PROCESOS_VALIDOS:
        return _error('Selecciona el proceso (STAMPING o EMBOSSING).')

    # Se vuelve a consultar la API acá: no se confía en lo que mostró el navegador.
    info = buscar_datos_externos(of_origen, proceso)
    try:
        resultado = servicio.registrar_fabricacion(
            of_origen=of_origen,
            proceso=proceso,
            info_externa=info,
            normalizar_of=_normalizar_of,
            datos_tecnicos=data.get('tecnicos') or {},
            maquina_id=data.get('maquina_id'),
            datos_manuales={
                'cliente': data.get('cliente'),
                'referencia': data.get('referencia'),
                'sobre': data.get('sobre'),
            },
            usuario=request.user,
            tipo=str(data.get('tipo') or '').strip().upper() or None,
        )
    except servicio.ErrorGrabado as e:
        return _error(e.mensaje, status=e.status)

    grabado, prueba = resultado['grabado'], resultado['prueba_k1']
    if resultado['accion'] == servicio.ACCION_REPETICION:
        mensaje = f'Refabricación registrada. El grabado {grabado.of_origen} {grabado.proceso} vuelve a APROBADO.'
    elif prueba is None:
        mensaje = (f'Grabado de producción registrado. El grabado {grabado.of_origen} {grabado.proceso} '
                   'queda APROBADO, sin K1.')
    else:
        mensaje = (f'Fabricación registrada. El grabado {grabado.of_origen} {grabado.proceso} '
                   f'queda pendiente de K1 (intento {prueba.intento}, {prueba.maquina}).')

    respuesta = {
        'status': 'ok',
        'message': mensaje,
        'accion': resultado['accion'],
        'estado': grabado.estado,
        'tipo': grabado.tipo,
    }
    if resultado['bano']:
        respuesta['bano'] = resultado['bano']
    return JsonResponse(respuesta)


# ============================================================
# K1 PENDIENTES (supervisores)
# ============================================================

def _requiere_permiso_k1(vista):
    """Igual que permission_required pero devolviendo 403 en JSON para las APIs."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.has_perm(PERMISO_DECIDIR_K1):
            return _error('No tienes permiso para decidir pruebas K1.', status=403)
        return vista(request, *args, **kwargs)
    return login_required(envoltura)


@login_required
@permission_required(PERMISO_DECIDIR_K1, raise_exception=True)
def k1_pendientes(request):
    return render(request, 'k1_pendientes.html')


@_requiere_permiso_k1
def api_k1_pendientes(request):
    pruebas = (PruebaK1.objects.filter(resultado='PENDIENTE')
               .select_related('grabado', 'maquina', 'fabricacion', 'fabricacion__registrado_por')
               .order_by('creado_el'))
    # Solo lo que muestra la tabla; el reporte completo sale de api_grabado_detalle.
    datos = []
    for p in pruebas:
        f = p.fabricacion
        datos.append({
            'id': p.id,
            'grabado_id': p.grabado_id,
            'of': p.grabado.of_origen,
            'proceso': p.grabado.proceso,
            'cliente': p.grabado.cliente,
            'referencia': p.grabado.referencia,
            'maquina': p.maquina.nombre,
            'intento': p.intento,
            'registrado_por': f.registrado_por.username if f.registrado_por else '—',
            'registrado_el': selectors.formatear_fecha(f.registrado_el),
            'es_propio': servicio.es_autor_de_k1(request.user, p),
            'puede_decidir': servicio.puede_decidir_k1(request.user, p),
        })
    return JsonResponse({'status': 'ok', 'data': datos})


# ============================================================
# DETALLE DE GRABADO (solo lectura, reutilizable)
# ============================================================

@login_required
def api_grabado_detalle(request, grabado_id):
    """Reporte completo de un grabado: datos generales, K1 actual, historial de
    fabricaciones e intentos K1, y envíos a máquina. Solo lectura; lo usa el
    panel lateral (static/js/componentes/panel_grabado.js)."""
    if request.method != 'GET':
        return _error('Método no permitido', status=405)
    detalle = selectors.detalle_grabado(grabado_id, request.user)
    if detalle is None:
        return _error('El grabado no existe.', status=404)
    return JsonResponse({'status': 'ok', 'data': detalle})


def _decidir(request, prueba_id, aprobar):
    if request.method != 'POST':
        return _error('Método no permitido', status=405)
    data = _leer_json(request)
    if data is None:
        return _error('Datos inválidos.')
    try:
        prueba = servicio.decidir_k1(
            prueba_id=prueba_id, usuario=request.user, aprobar=aprobar, motivo=data.get('motivo'),
        )
    except servicio.ErrorGrabado as e:
        return _error(e.mensaje, status=e.status)

    if aprobar:
        mensaje = f'K1 aprobado. El grabado {prueba.grabado.of_origen} {prueba.grabado.proceso} queda APROBADO.'
    else:
        mensaje = (f'K1 rechazado. El grabado {prueba.grabado.of_origen} {prueba.grabado.proceso} '
                   'vuelve a En Fabricación.')
    return JsonResponse({'status': 'ok', 'message': mensaje})


@_requiere_permiso_k1
def api_k1_aprobar(request, prueba_id):
    return _decidir(request, prueba_id, aprobar=True)


@_requiere_permiso_k1
def api_k1_rechazar(request, prueba_id):
    return _decidir(request, prueba_id, aprobar=False)


# ============================================================
# INVENTARIO DE GRABADOS (consulta, cualquier usuario con sesión)
# ============================================================

@login_required
def inventario_grabados(request):
    return render(request, 'inventario_grabados.html', {
        'estados': Grabado.ESTADO_CHOICES,
        'tipos': Grabado.TIPO_CHOICES,
    })


@login_required
def api_inventario(request):
    """Conteos por estado y listado de grabados, con filtros opcionales
    ?q=texto&proceso=STAMPING|EMBOSSING&estado=<estado>&tipo=K1|DIRECTO|LEGADO. Solo lectura."""
    if request.method != 'GET':
        return _error('Método no permitido', status=405)
    q = request.GET.get('q', '').strip()
    proceso = request.GET.get('proceso', '').strip().upper()
    estado = request.GET.get('estado', '').strip().upper()
    tipo = request.GET.get('tipo', '').strip().upper()
    if proceso and proceso not in servicio.PROCESOS_VALIDOS:
        return _error('Proceso inválido.')
    if estado and estado not in selectors.ESTADOS_GRABADO:
        return _error('Estado inválido.')
    if tipo and tipo not in selectors.TIPOS_GRABADO:
        return _error('Tipo inválido.')
    return JsonResponse({'status': 'ok',
                         **selectors.inventario_grabados(q=q, proceso=proceso, estado=estado, tipo=tipo)})


# ============================================================
# PLANI (fase 3): mandar a máquina y recoger
# ============================================================

@login_required
def api_plani_mandar(request):
    """Manda a máquina el grabado de una OF del PLANI. JSON:
    {of, proceso, grabado_id, fila: {maquina, fecha_programada, cantidad_formatos,
    horas_proceso, papel}}. La máquina se resuelve contra el catálogo en el servidor."""
    if request.method != 'POST':
        return _error('Método no permitido', status=405)
    data = _leer_json(request)
    if data is None:
        return _error('Datos inválidos.')
    of = _of_normalizada(str(data.get('of', '')).strip())
    proceso = str(data.get('proceso', '')).strip().upper()
    try:
        envio = servicio.mandar_a_maquina(
            of=of, proceso=proceso, grabado_id=data.get('grabado_id'),
            fila=data.get('fila') or {}, usuario=request.user,
        )
    except servicio.ErrorGrabado as e:
        return _error(e.mensaje, status=e.status)
    return JsonResponse({
        'status': 'ok',
        'message': f'OF {envio.of} en máquina ({envio.maquina}) con el grabado '
                   f'{envio.grabado.of_origen} {envio.grabado.proceso}.',
        'envio_id': envio.id,
    })


@login_required
def api_plani_recoger(request):
    """Recoge de máquina el grabado de una OF. multipart/form-data:
    envio_id, of, estado_fisico (OK | REPETIR), ubicacion, comentario."""
    if request.method != 'POST':
        return _error('Método no permitido', status=405)
    datos = request.POST
    try:
        envio = servicio.recoger_de_maquina(
            envio_id=datos.get('envio_id'),
            of=_of_normalizada(str(datos.get('of', '')).strip()),
            estado_fisico=datos.get('estado_fisico'),
            ubicacion=datos.get('ubicacion'),
            comentario=datos.get('comentario'),
            usuario=request.user,
        )
    except servicio.ErrorGrabado as e:
        return _error(e.mensaje, status=e.status)
    grabado = envio.grabado
    if envio.estado_fisico == 'OK':
        mensaje = (f'Grabado {grabado.of_origen} recogido y guardado en {envio.ubicacion}. '
                   f'Usos acumulados: {grabado.usos_acumulados}.')
    else:
        mensaje = f'Grabado {grabado.of_origen} recogido y marcado para REPETIR.'
    return JsonResponse({'status': 'ok', 'message': mensaje})

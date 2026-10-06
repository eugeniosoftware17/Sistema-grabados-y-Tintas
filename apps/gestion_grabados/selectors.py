"""
Consultas de solo lectura sobre los modelos nuevos (grabado como entidad).
No escriben nada: arman diccionarios listos para JsonResponse, para que
cualquier pantalla (K1 Pendientes, más adelante la consulta de grabados)
reutilice el mismo formato.
"""
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import EnvioMaquina, FabricacionGrabado, Grabado, PruebaK1
from .services.grabados import (
    es_auto_decision, es_autor_de_k1, of_origen_externa, proceso_ausente_en_externo, puede_decidir_k1,
)


def formatear_fecha(valor):
    """dd/mm/aaaa hh:mm en la zona horaria de settings.TIME_ZONE (o None)."""
    if valor is None:
        return None
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


def _usuario(usuario):
    return usuario.username if usuario else None


def datos_tecnicos(fabricacion):
    return {
        'responsables': fabricacion.responsables,
        'peso_inicial': fabricacion.peso_inicial,
        'peso_final': fabricacion.peso_final,
        'perdida': fabricacion.perdida,
        'temp': fabricacion.temp,
        'rpm': fabricacion.rpm,
        'tiempo': fabricacion.tiempo,
        'compensacion': fabricacion.compensacion,
        'compensacion_motivo': fabricacion.compensacion_motivo,
        'bano_ml': round(fabricacion.bano_ml, 2) if fabricacion.bano_ml is not None else None,
    }


def _fabricacion(fabricacion):
    return {
        'id': fabricacion.id,
        'numero': fabricacion.numero,
        'tipo': fabricacion.tipo,
        'tipo_display': fabricacion.get_tipo_display(),
        'registrado_por': _usuario(fabricacion.registrado_por),
        'registrado_el': formatear_fecha(fabricacion.registrado_el),
        'tecnicos': datos_tecnicos(fabricacion),
    }


def _prueba(prueba):
    return {
        'id': prueba.id,
        'intento': prueba.intento,
        'fabricacion_numero': prueba.fabricacion.numero,
        'maquina': prueba.maquina.nombre,
        'resultado': prueba.resultado,
        'resultado_display': prueba.get_resultado_display(),
        'motivo_rechazo': prueba.motivo_rechazo,
        'creado_por': _usuario(prueba.creado_por),
        'creado_el': formatear_fecha(prueba.creado_el),
        'decidido_por': _usuario(prueba.decidido_por),
        'decidido_el': formatear_fecha(prueba.decidido_el),
        # Decidido por quien registró la fabricación (solo superusuarios): se
        # muestra como "Auto-aprobado" / "Auto-rechazado".
        'auto_decision': es_auto_decision(prueba),
    }


def _envio(envio):
    return {
        'id': envio.id,
        'of': envio.of,
        'maquina': envio.maquina.nombre,
        'enviado_por': _usuario(envio.enviado_por),
        'enviado_el': formatear_fecha(envio.enviado_el),
        'recogido_por': _usuario(envio.recogido_por),
        'recogido_el': formatear_fecha(envio.recogido_el),
        'estado_fisico': envio.estado_fisico,
        'comentario': envio.comentario,
        'ubicacion': envio.ubicacion,
        'abierto': envio.recogido_el is None,
    }


def detalle_grabado(grabado_id, usuario):
    """Reporte completo de un grabado, o None si no existe.
    'k1_actual' es el K1 pendiente (si hay), con la fabricación que prueba, si
    `usuario` es su autor y si puede decidirlo (ver services.puede_decidir_k1)."""
    grabado = (
        Grabado.objects.select_related('creado_por')
        .prefetch_related(
            Prefetch('fabricaciones',
                     queryset=FabricacionGrabado.objects.select_related('registrado_por').order_by('numero')),
            Prefetch('pruebas_k1',
                     queryset=PruebaK1.objects.select_related(
                         'maquina', 'fabricacion', 'creado_por', 'decidido_por').order_by('intento')),
            Prefetch('envios',
                     queryset=EnvioMaquina.objects.select_related(
                         'maquina', 'enviado_por', 'recogido_por').order_by('-enviado_el')),
        )
        .filter(pk=grabado_id)
        .first()
    )
    if grabado is None:
        return None

    pruebas = list(grabado.pruebas_k1.all())
    pendiente = next((p for p in pruebas if p.resultado == 'PENDIENTE'), None)

    k1_actual = None
    if pendiente is not None:
        k1_actual = _prueba(pendiente)
        k1_actual.update(
            fabricacion=_fabricacion(pendiente.fabricacion),
            es_propio=es_autor_de_k1(usuario, pendiente),
            puede_decidir=puede_decidir_k1(usuario, pendiente),
        )

    return {
        'id': grabado.id,
        'of_origen': grabado.of_origen,
        'proceso': grabado.proceso,
        'estado': grabado.estado,
        'estado_display': grabado.get_estado_display(),
        'cliente': grabado.cliente,
        'referencia': grabado.referencia,
        'sobre': grabado.sobre,
        'datos_manuales': grabado.datos_manuales,
        'tipo': grabado.tipo,
        'tipo_display': grabado.get_tipo_display(),
        'ubicacion': grabado.ubicacion,
        'usos_acumulados': grabado.usos_acumulados,
        'creado_por': _usuario(grabado.creado_por),
        'creado_el': formatear_fecha(grabado.creado_el),
        'k1_actual': k1_actual,
        'fabricaciones': [_fabricacion(f) for f in grabado.fabricaciones.all()],
        'pruebas_k1': [_prueba(p) for p in pruebas],
        'envios': [_envio(e) for e in grabado.envios.all()],
    }


# ============================================================
# INVENTARIO DE GRABADOS
# ============================================================

ESTADOS_GRABADO = [codigo for codigo, _ in Grabado.ESTADO_CHOICES]
TIPOS_GRABADO = [codigo for codigo, _ in Grabado.TIPO_CHOICES]
RESULTADOS_K1 = dict(PruebaK1.RESULTADO_CHOICES)

# Tope de filas por respuesta; con más resultados la pantalla pide afinar la búsqueda.
LIMITE_INVENTARIO = 500


def _filtrar_busqueda(queryset, q):
    """Cada palabra de `q` tiene que aparecer en la OF de origen, el cliente o la referencia."""
    for palabra in (q or '').split():
        queryset = queryset.filter(
            Q(of_origen__icontains=palabra) | Q(cliente__icontains=palabra) | Q(referencia__icontains=palabra)
        )
    return queryset


def inventario_grabados(q='', proceso='', estado='', tipo=''):
    """Grabados para la pantalla de Inventario.
    - 'conteos': grabados por estado, respetando búsqueda, proceso y tipo (no el
      filtro de estado, para que las tarjetas muestren el reparto completo).
    - 'filas': hasta LIMITE_INVENTARIO grabados, del más nuevo al más viejo.
    - 'total': cuántos cumplen todos los filtros (puede superar el límite)."""
    base = _filtrar_busqueda(Grabado.objects.all(), q)
    if proceso:
        base = base.filter(proceso=proceso)
    if tipo:
        base = base.filter(tipo=tipo)

    conteos = dict.fromkeys(ESTADOS_GRABADO, 0)
    for fila in base.values('estado').annotate(n=Count('id')).order_by():
        conteos[fila['estado']] = fila['n']

    filtrados = base.filter(estado=estado) if estado else base
    total = filtrados.count()

    # Subconsultas (y no Count + GROUP BY): SQL Server no permite agrupar por
    # una expresión que contenga otra subconsulta.
    pruebas = PruebaK1.objects.filter(grabado=OuterRef('pk'))
    intentos = pruebas.order_by().values('grabado').annotate(n=Count('id')).values('n')
    ultimo = pruebas.order_by('-intento').values('resultado')[:1]

    filas = (filtrados
             .annotate(intentos_k1=Coalesce(Subquery(intentos, output_field=IntegerField()), Value(0)),
                       ultimo_k1=Subquery(ultimo))
             .order_by('-creado_el', '-id')
             .values('id', 'of_origen', 'proceso', 'cliente', 'referencia', 'estado', 'tipo',
                     'intentos_k1', 'ultimo_k1', 'usos_acumulados', 'ubicacion', 'creado_el')
             [:LIMITE_INVENTARIO])

    estados_display = dict(Grabado.ESTADO_CHOICES)
    tipos_display = dict(Grabado.TIPO_CHOICES)
    datos = [{
        'id': f['id'],
        'of_origen': f['of_origen'],
        'proceso': f['proceso'],
        'cliente': f['cliente'],
        'referencia': f['referencia'],
        'estado': f['estado'],
        'estado_display': estados_display.get(f['estado'], f['estado']),
        'tipo': f['tipo'],
        'tipo_display': tipos_display.get(f['tipo'], f['tipo']),
        'intentos_k1': f['intentos_k1'],
        'ultimo_k1': f['ultimo_k1'],
        'ultimo_k1_display': RESULTADOS_K1.get(f['ultimo_k1']),
        'usos_acumulados': f['usos_acumulados'],
        'ubicacion': f['ubicacion'],
        'creado_el': timezone.localtime(f['creado_el']).strftime('%d/%m/%Y') if f['creado_el'] else None,
    } for f in filas]

    return {'conteos': conteos, 'total': total, 'limite': LIMITE_INVENTARIO, 'data': datos}


# ============================================================
# PLANI (fase 3): estado del grabado de cada fila del Excel
# ============================================================

# Acción que muestra cada fila del PLANI (la decide el servidor, el JS solo la pinta).
PLANI_SIN_PROCESO = 'SIN_PROCESO'          # la OF existe en el externo pero sin este proceso
PLANI_DAR_DE_ALTA = 'DAR_DE_ALTA'          # no hay grabado registrado
PLANI_SIN_ACCION = 'SIN_ACCION'            # en fabricación / pendiente de K1
PLANI_MANDAR = 'MANDAR'                    # aprobado y libre
PLANI_MANDAR_OTRA_VEZ = 'MANDAR_OTRA_VEZ'  # aprobado y esta OF ya se tiró (envío cerrado OK)
PLANI_RECOGER = 'RECOGER'                  # en máquina con un envío de esta OF
PLANI_EN_MAQUINA_OTRA = 'EN_MAQUINA_OTRA'  # en máquina con un envío de otra OF
PLANI_REFABRICAR = 'REFABRICAR'            # marcado para REPETIR


def _fecha_corta(valor):
    return timezone.localtime(valor).strftime('%d/%m') if valor else None


def estado_grabados_para_plani(filas, normalizar_of):
    """Agrega a cada fila del PLANI (dicts con 'of', 'proceso' y los campos *_ext
    de buscar_datos_externos) la clave 'grabado' con el grabado que le toca, su
    estado y la acción a mostrar. Solo lectura, en pocas consultas por lote.

    El grabado se resuelve con la OF Referencia de la API para el proceso de la
    fila (vacía o igual a la propia OF -> la OF es de origen); si la OF no está en
    la API se usa la propia OF (alta manual). Si la OF ya está en máquina con
    cualquier grabado, manda ese envío (puede ser otro grabado elegido a mano)."""
    claves = []
    for fila in filas:
        proceso, of = fila['proceso'], str(fila['of'])
        if proceso_ausente_en_externo(fila, proceso, normalizar_of):
            claves.append(None)
        else:
            claves.append((of_origen_externa(fila, proceso, normalizar_of) or of, proceso))

    origenes = {c[0] for c in claves if c}
    grabados = {(g.of_origen, g.proceso): g for g in Grabado.objects.filter(of_origen__in=origenes)} if origenes else {}

    abiertos = list(EnvioMaquina.objects.filter(recogido_el__isnull=True).select_related('grabado', 'maquina'))
    abierto_por_of = {(e.of, e.grabado.proceso): e for e in abiertos}
    abierto_por_grabado = {e.grabado_id: e for e in abiertos}

    ofs = {str(f['of']) for f in filas}
    ultimo_cerrado = {}
    if ofs:
        for e in (EnvioMaquina.objects.filter(of__in=ofs, recogido_el__isnull=False)
                  .select_related('grabado').order_by('-recogido_el')):
            ultimo_cerrado.setdefault((e.of, e.grabado.proceso), e)

    for fila, clave in zip(filas, claves):
        fila['grabado'] = _estado_fila_plani(
            fila, clave, grabados, abierto_por_of, abierto_por_grabado, ultimo_cerrado)
    return filas


def _resumen_grabado_plani(grabado, of):
    return {
        'id': grabado.id,
        'of_origen': grabado.of_origen,
        'proceso': grabado.proceso,
        'estado': grabado.estado,
        'estado_display': grabado.get_estado_display(),
        'tipo': grabado.tipo,
        'tipo_display': grabado.get_tipo_display(),
        'ubicacion': grabado.ubicacion,
        'usa_grabado_de_otra': grabado.of_origen != of,
    }


def _estado_fila_plani(fila, clave, grabados, abierto_por_of, abierto_por_grabado, ultimo_cerrado):
    proceso, of = fila['proceso'], str(fila['of'])
    if clave is None:
        return {'accion': PLANI_SIN_PROCESO, 'grabado': None,
                'mensaje': f'El sistema externo no tiene {proceso} para esta OF.'}

    propio = abierto_por_of.get((of, proceso))
    if propio is not None:
        return {
            'accion': PLANI_RECOGER,
            'grabado': _resumen_grabado_plani(propio.grabado, of),
            'envio': {'id': propio.id, 'of': propio.of, 'maquina': propio.maquina.nombre,
                      'enviado_el': formatear_fecha(propio.enviado_el)},
            'terminada_en_planta': fila.get('acabado_ext') == '1',
            'mensaje': f'En máquina ({propio.maquina.nombre}) desde el {formatear_fecha(propio.enviado_el)}.',
        }

    grabado = grabados.get(clave)
    if grabado is None:
        of_origen = clave[0]
        mensaje = ('No hay grabado registrado para esta OF.' if of_origen == of else
                   f'Usa el grabado de la OF {of_origen}, que no está registrado.')
        return {'accion': PLANI_DAR_DE_ALTA, 'grabado': None, 'mensaje': mensaje,
                'alta': {'of': of_origen, 'proceso': proceso}}

    resumen = _resumen_grabado_plani(grabado, of)
    if grabado.estado in ('EN_FABRICACION', 'PENDIENTE_K1'):
        return {'accion': PLANI_SIN_ACCION, 'grabado': resumen, 'mensaje': grabado.get_estado_display()}
    if grabado.estado == 'REPETIR':
        return {'accion': PLANI_REFABRICAR, 'grabado': resumen, 'mensaje': 'Marcado para repetir.',
                'alta': {'of': grabado.of_origen, 'proceso': proceso}}
    if grabado.estado == 'EN_MAQUINA':
        otro = abierto_por_grabado.get(grabado.id)
        mensaje = f'En máquina con la OF {otro.of}' if otro else 'En máquina (sin envío registrado).'
        return {'accion': PLANI_EN_MAQUINA_OTRA if otro else PLANI_SIN_ACCION,
                'grabado': resumen, 'mensaje': mensaje}

    # APROBADO: libre. Si esta OF ya se tiró y se recogió OK, se marca como completada.
    ultimo = ultimo_cerrado.get((of, proceso))
    if ultimo is not None and ultimo.estado_fisico == 'OK':
        return {'accion': PLANI_MANDAR_OTRA_VEZ, 'grabado': resumen,
                'completada': {'fecha': _fecha_corta(ultimo.recogido_el), 'ubicacion': ultimo.ubicacion},
                'mensaje': f'Completada el {_fecha_corta(ultimo.recogido_el)}.'}
    return {'accion': PLANI_MANDAR, 'grabado': resumen, 'mensaje': 'Aprobado y disponible.'}

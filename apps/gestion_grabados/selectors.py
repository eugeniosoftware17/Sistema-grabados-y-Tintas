"""
Consultas de solo lectura sobre los modelos nuevos (grabado como entidad).
No escriben nada: arman diccionarios listos para JsonResponse, para que
cualquier pantalla (K1 Pendientes, más adelante la consulta de grabados)
reutilice el mismo formato.
"""
from datetime import date

from django.db.models import Case, Count, F, IntegerField, OuterRef, Prefetch, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from .management.commands.auditar_grabados import normalizar_of, referencia_valida
from .models import EnvioMaquina, FabricacionGrabado, Grabado, HistorialUbicacion, OrdenFabricacion, PruebaK1
from .services.grabados import (
    PROCESOS_VALIDOS, es_auto_decision, of_origen_externa, proceso_ausente_en_externo,
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
        # Decidido por quien registró la fabricación: se muestra como
        # "Auto-aprobado" / "Auto-rechazado" (solo informativo).
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


def _cambio_ubicacion(cambio):
    return {
        'id': cambio.id,
        'anterior': cambio.ubicacion_anterior,
        'nueva': cambio.ubicacion_nueva,
        'origen': cambio.origen,
        'origen_display': cambio.get_origen_display(),
        'usuario': _usuario(cambio.usuario),
        'cambiado_el': formatear_fecha(cambio.cambiado_el),
    }


def detalle_grabado(grabado_id):
    """Reporte completo de un grabado, o None si no existe.
    'k1_actual' es el K1 pendiente (si hay), con la fabricación que prueba."""
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
            Prefetch('historial_ubicaciones',
                     queryset=HistorialUbicacion.objects.select_related('usuario')
                     .order_by('-cambiado_el', '-id')),
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
        k1_actual['fabricacion'] = _fabricacion(pendiente.fabricacion)

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
        'historial_ubicaciones': [_cambio_ubicacion(c) for c in grabado.historial_ubicaciones.all()],
    }


# ============================================================
# TABLA DE CONSULTAS (histórico): grabado de cada OrdenFabricacion
# ============================================================

ENLACE_LEGADO = 'LEGADO'    # LegadoOrden.grabado (lo deja migrar_a_grabados)
ENLACE_ENVIO = 'ENVIO'      # la OF se tiró con el PLANI nuevo (EnvioMaquina)
ENLACE_OF = 'OF'            # Grabado(of_origen = OF Referencia o la propia OF, proceso)

MOTIVO_OF_INVALIDA = 'La OF o el proceso de esta fila no son válidos: no se puede enlazar con un grabado.'
MOTIVO_SIN_MIGRAR = 'Fila sin migrar (SIN_MIGRAR) y sin grabado en EIS: la ubicación no se puede editar.'
MOTIVO_SIN_GRABADO = ('Esta OF no tiene grabado en EIS (falta migrarla o crearlo): '
                      'la ubicación no se puede editar.')


def grabados_de_ordenes(ordenes):
    """Resuelve el grabado de cada fila de OrdenFabricacion, en cascada:
    1. LegadoOrden.grabado (si la fila ya se migró y quedó con grabado);
    2. el último EnvioMaquina de esa OF con un grabado del mismo proceso;
    3. Grabado(of_origen = OF Referencia válida o la propia OF, proceso), la
       misma regla con la que migrar_a_grabados agrupa las filas.
    `ordenes`: dicts con 'id', 'of', 'referencia', 'proceso' y 'legado_grabado'
    (legado__grabado_id), 'legado_rol' (legado__rol). Devuelve
    {orden_id: {'grabado': {...} | None, 'enlace': ..., 'motivo': ...}}.
    Tres consultas en total, sin importar cuántas filas: los grabados y envíos
    se leen enteros (sin IN con miles de parámetros, SQL Server admite 2100)."""
    grabados = {g['id']: g for g in Grabado.objects.values('id', 'of_origen', 'proceso', 'ubicacion',
                                                            'actualizado_el')}
    por_origen = {(g['of_origen'], g['proceso']): g['id'] for g in grabados.values()}
    por_envio = {}
    for e in (EnvioMaquina.objects.order_by('-enviado_el', '-id')
              .values('of', 'grabado_id', 'grabado__proceso')):
        por_envio.setdefault((normalizar_of(e['of']), e['grabado__proceso']), e['grabado_id'])

    resultado = {}
    for o in ordenes:
        of_norm = normalizar_of(o['of'])
        proceso = (o['proceso'] or '').strip().upper()
        grabado_id, enlace = o.get('legado_grabado'), ENLACE_LEGADO
        if grabado_id is None and of_norm:
            grabado_id, enlace = por_envio.get((of_norm, proceso)), ENLACE_ENVIO
            if grabado_id is None:
                origen = referencia_valida({'referencia': o['referencia'], 'of_norm': of_norm}) or of_norm
                grabado_id, enlace = por_origen.get((origen, proceso)), ENLACE_OF

        grabado = grabados.get(grabado_id)
        if grabado is not None:
            resultado[o['id']] = {'grabado': {
                'id': grabado['id'],
                'of_origen': grabado['of_origen'],
                'proceso': grabado['proceso'],
                'ubicacion': grabado['ubicacion'],
                'actualizado_el': formatear_fecha(grabado['actualizado_el']),
            }, 'enlace': enlace, 'motivo': None}
        else:
            if not of_norm or proceso not in PROCESOS_VALIDOS:
                motivo = MOTIVO_OF_INVALIDA
            elif o.get('legado_rol') == 'SIN_MIGRAR':
                motivo = MOTIVO_SIN_MIGRAR
            else:
                motivo = MOTIVO_SIN_GRABADO
            resultado[o['id']] = {'grabado': None, 'enlace': None, 'motivo': motivo}
    return resultado


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
    - 'filas': hasta LIMITE_INVENTARIO grabados, del modificado más reciente al
      más viejo (Grabado.actualizado_el).
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
             .order_by('-actualizado_el', '-id')
             .values('id', 'of_origen', 'proceso', 'cliente', 'referencia', 'estado', 'tipo',
                     'intentos_k1', 'ultimo_k1', 'usos_acumulados', 'ubicacion', 'creado_el',
                     'actualizado_el')
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
        'actualizado_el': formatear_fecha(f['actualizado_el']),
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


# ---------------------------------------------------------------- Tabla de Consultas (Histórico)

LIMITE_CONSULTA = 500

# Columna de la Tabla de Consultas -> campo de OrdenFabricacion que se filtra.
# 'ubicacion' es la que se muestra: la del grabado enlazado por LegadoOrden o, sin él,
# la de la fila (los otros enlaces de grabados_de_ordenes no entran en el filtro).
CAMPOS_CONSULTA = {
    'of': 'of', 'ref': 'referencia', 'cliente': 'cliente', 'desc': 'descripcion',
    'estado': 'estado', 'proceso': 'proceso', 'maquina': 'maquina', 'tipo': 'tipo_grabado',
    'ubicacion': 'ubicacion_mostrada', 'sobre': 'sobre', 'cantidad': 'cantidad_formatos',
    'horas': 'horas_proceso', 'responsables': 'responsables', 'peso_i': 'peso_inicial',
    'peso_f': 'peso_final', 'perdida': 'perdida', 'temp': 'temp', 'rpm': 'rpm',
    'tiempo_t': 'tiempo', 'comp': 'compensacion',
}
COLUMNAS_NUMERICAS_CONSULTA = {'cantidad', 'horas', 'peso_i', 'peso_f', 'perdida', 'temp', 'rpm'}
CAMPOS_FECHA_CONSULTA = {'fecha': 'fecha_programada', 'fecha_reg': 'fecha_registro'}
VACIO_CONSULTA = '—'  # lo que la pantalla muestra para null o ''
# SQL Server admite como mucho 2100 parámetros por consulta.
MAX_VALORES_FILTRO = 2000


class FiltroConsultaInvalido(ValueError):
    pass


def _ordenes_base(q=''):
    """OrdenFabricacion con la ubicación que muestra la tabla y la búsqueda `q`
    (cada palabra en OF, OF Referencia, cliente, descripción, proceso, ubicación o estado)."""
    queryset = OrdenFabricacion.objects.annotate(ubicacion_mostrada=Case(
        When(legado__grabado__isnull=False, then=F('legado__grabado__ubicacion')),
        default=F('ubicacion'),
    ))
    for palabra in (q or '').split():
        queryset = queryset.filter(
            Q(of__icontains=palabra) | Q(referencia__icontains=palabra) | Q(cliente__icontains=palabra)
            | Q(descripcion__icontains=palabra) | Q(proceso__icontains=palabra) | Q(ubicacion__icontains=palabra)
            | Q(legado__grabado__ubicacion__icontains=palabra) | Q(estado__icontains=palabra)
        )
    return queryset


def _condicion_valores(columna, valores):
    """Q de las filas cuyo valor mostrado en `columna` está en `valores` (textos de pantalla)."""
    campo = CAMPOS_CONSULTA[columna]
    condicion = Q(pk__in=[])
    reales = [v for v in valores if v != VACIO_CONSULTA]
    if VACIO_CONSULTA in valores:
        condicion |= Q(**{f'{campo}__isnull': True})
        if columna not in COLUMNAS_NUMERICAS_CONSULTA:
            condicion |= Q(**{campo: ''})
    if reales:
        if columna in COLUMNAS_NUMERICAS_CONSULTA:
            try:
                reales = [float(v) for v in reales]
            except ValueError:
                raise FiltroConsultaInvalido(f'Valor no numérico en {columna}.')
        condicion |= Q(**{f'{campo}__in': reales})
    return condicion


def _aplicar_filtros_consulta(queryset, filtros):
    """`filtros` = lista de {'col': columna, 'in': [...]} | {'col', 'fuera': [...]} |
    {'col', 'desde': 'aaaa-mm-dd', 'hasta': ...}; se cumplen todos (una columna puede
    repetirse, ej. el filtro de Estado del panel y el de la columna)."""
    if not isinstance(filtros or [], list):
        raise FiltroConsultaInvalido('Filtros inválidos.')
    for filtro in filtros or []:
        if not isinstance(filtro, dict):
            raise FiltroConsultaInvalido('Filtros inválidos.')
        columna = filtro.get('col')
        if not isinstance(columna, str):
            raise FiltroConsultaInvalido('Filtros inválidos.')
        if columna in CAMPOS_FECHA_CONSULTA:
            campo = CAMPOS_FECHA_CONSULTA[columna]
            try:
                if filtro.get('desde'):
                    queryset = queryset.filter(**{f'{campo}__gte': date.fromisoformat(filtro['desde'])})
                if filtro.get('hasta'):
                    queryset = queryset.filter(**{f'{campo}__lte': date.fromisoformat(filtro['hasta'])})
            except (TypeError, ValueError):
                raise FiltroConsultaInvalido(f'Fecha inválida en {columna}.')
            continue
        if columna not in CAMPOS_CONSULTA:
            raise FiltroConsultaInvalido(f'Columna desconocida: {columna}.')
        incluir, excluir = filtro.get('in'), filtro.get('fuera')
        valores = incluir if incluir is not None else excluir
        if not isinstance(valores, list) or not all(isinstance(v, str) for v in valores):
            raise FiltroConsultaInvalido(f'Filtro inválido en {columna}.')
        if len(valores) > MAX_VALORES_FILTRO:
            raise FiltroConsultaInvalido('Demasiados valores marcados; usa la búsqueda para afinar.')
        condicion = _condicion_valores(columna, valores)
        queryset = queryset.filter(condicion) if incluir is not None else queryset.exclude(condicion)
    return queryset


def consulta_ordenes(q='', filtros=None):
    """Filas de OrdenFabricacion para la Tabla de Consultas: búsqueda y filtros sobre
    todo el historial, de la registrada más recientemente a la más vieja
    (fecha_registro, luego id), con tope LIMITE_CONSULTA.
    Devuelve {'total': cuántas cumplen, 'limite', 'ordenes': queryset recortado}."""
    queryset = _aplicar_filtros_consulta(_ordenes_base(q), filtros)
    total = queryset.count()
    ordenes = queryset.order_by(F('fecha_registro').desc(nulls_last=True), '-id')[:LIMITE_CONSULTA]
    return {'total': total, 'limite': LIMITE_CONSULTA, 'ordenes': ordenes}


def valores_columna_consulta(columna, q=''):
    """Valores distintos de una columna en todo el historial (respetando la búsqueda),
    para la lista del filtro por columna. Sin orden: lo ordena la pantalla."""
    if columna not in CAMPOS_CONSULTA:
        raise FiltroConsultaInvalido(f'Columna desconocida: {columna}.')
    return list(_ordenes_base(q).order_by().values_list(CAMPOS_CONSULTA[columna], flat=True).distinct())

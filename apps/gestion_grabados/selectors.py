"""
Consultas de solo lectura sobre los modelos nuevos (grabado como entidad).
No escriben nada: arman diccionarios listos para JsonResponse, para que
cualquier pantalla (K1 Pendientes, más adelante la consulta de grabados)
reutilice el mismo formato.
"""
from datetime import date

from django.db.models import Count, IntegerField, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .management.commands.auditar_grabados import normalizar_of, referencia_valida
from .management.commands.migrar_a_grabados import MAQUINA_SIN_NOMBRE
from .models import (
    EnvioMaquina, FabricacionGrabado, Grabado, HistorialActivacion, HistorialUbicacion,
    PruebaK1, Sobre,
)
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


def _activacion(cambio):
    return {
        'id': cambio.id,
        'accion': cambio.accion,
        'accion_display': cambio.get_accion_display(),
        'motivo': cambio.motivo,
        'estado': cambio.estado,
        'estado_display': cambio.get_estado_display(),
        'usuario': _usuario(cambio.usuario),
        'fecha': formatear_fecha(cambio.fecha),
    }


def detalle_grabado(grabado_id):
    """Reporte completo de un grabado, o None si no existe.
    'k1_actual' es el K1 pendiente (si hay), con la fabricación que prueba."""
    grabado = (
        Grabado.objects.select_related('creado_por', 'desactivado_por')
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
            Prefetch('historial_activaciones',
                     queryset=HistorialActivacion.objects.select_related('usuario').order_by('-fecha', '-id')),
        )
        .filter(pk=grabado_id)
        .first()
    )
    if grabado is None:
        return None

    pruebas = list(grabado.pruebas_k1.all())
    pendiente = next((p for p in pruebas if p.resultado == 'PENDIENTE'), None)

    inactivo = grabado.estado == 'INACTIVO'
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
        # Activación: el panel muestra Desactivar o Reactivar según lo que decida el servidor.
        'inactivo': inactivo,
        'puede_desactivar': grabado.estado not in ('INACTIVO', 'EN_MAQUINA'),
        'desactivacion': {
            'por': _usuario(grabado.desactivado_por),
            'el': formatear_fecha(grabado.desactivado_el),
            'motivo': grabado.motivo_desactivacion,
            'estado_previo': grabado.estado_previo,
            'estado_previo_display': grabado.get_estado_previo_display(),
        } if inactivo else None,
        'historial_activaciones': [_activacion(c) for c in grabado.historial_activaciones.all()],
    }


# ============================================================
# HISTÓRICO (api/registros, Estadísticas): grabado de cada OrdenFabricacion
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
                                                            'estado', 'actualizado_el')}
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
                'estado': grabado['estado'],
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


# Filtros por columna del Inventario (mismo formato que la Tabla de Consultas, ver
# _aplicar_filtros_consulta): columna de la pantalla -> campo de Grabado o anotación.
CAMPOS_INVENTARIO = {
    'of': 'of_origen', 'proceso': 'proceso', 'cliente': 'cliente', 'referencia': 'referencia',
    'estado': 'estado', 'tipo': 'tipo', 'intentos': 'intentos_k1', 'ultimo_k1': 'ultimo_k1',
    'usos': 'usos_acumulados', 'ubicacion': 'ubicacion',
}
CAMPOS_FECHA_INVENTARIO = {'modificado': 'actualizado_el__date', 'creado': 'creado_el__date'}
COLUMNAS_NUMERICAS_INVENTARIO = {'intentos', 'usos'}
# Columnas que guardan un código: la pantalla muestra el texto pero filtra por el código.
ETIQUETAS_INVENTARIO = {
    'estado': dict(Grabado.ESTADO_CHOICES), 'tipo': dict(Grabado.TIPO_CHOICES), 'ultimo_k1': RESULTADOS_K1,
}


def _con_datos_k1(queryset):
    """Anota intentos_k1 (cuántas PruebaK1) y ultimo_k1 (resultado del último intento).
    Subconsultas (y no Count + GROUP BY): SQL Server no permite agrupar por una
    expresión que contenga otra subconsulta."""
    pruebas = PruebaK1.objects.filter(grabado=OuterRef('pk'))
    intentos = pruebas.order_by().values('grabado').annotate(n=Count('id')).values('n')
    ultimo = pruebas.order_by('-intento').values('resultado')[:1]
    return queryset.annotate(intentos_k1=Coalesce(Subquery(intentos, output_field=IntegerField()), Value(0)),
                             ultimo_k1=Subquery(ultimo))


def _filtrar_columnas_inventario(queryset, filtros):
    if not filtros:
        return queryset
    filtrados = _aplicar_filtros_consulta(_con_datos_k1(queryset), filtros, CAMPOS_INVENTARIO,
                                          CAMPOS_FECHA_INVENTARIO, COLUMNAS_NUMERICAS_INVENTARIO)
    # Por id: así los conteos por estado no agrupan sobre las subconsultas de K1.
    return queryset.filter(pk__in=filtrados.values('pk'))


def inventario_grabados(q='', proceso='', estado='', tipo='', filtros=None):
    """Grabados para la pantalla de Inventario.
    - 'conteos': grabados por estado, respetando búsqueda, proceso, tipo y filtros por
      columna (no el filtro de estado de las tarjetas, para mostrar el reparto completo).
    - 'filas': hasta LIMITE_INVENTARIO grabados, del modificado más reciente al
      más viejo (Grabado.actualizado_el).
    - 'total': cuántos cumplen todos los filtros (puede superar el límite).
    `filtros`: lista de filtros por columna (ver CAMPOS_INVENTARIO); uno inválido
    lanza FiltroConsultaInvalido."""
    base = _filtrar_busqueda(Grabado.objects.all(), q)
    if proceso:
        base = base.filter(proceso=proceso)
    if tipo:
        base = base.filter(tipo=tipo)
    base = _filtrar_columnas_inventario(base, filtros)

    conteos = dict.fromkeys(ESTADOS_GRABADO, 0)
    for fila in base.values('estado').annotate(n=Count('id')).order_by():
        conteos[fila['estado']] = fila['n']

    filtrados = base.filter(estado=estado) if estado else base
    total = filtrados.count()

    filas = (_con_datos_k1(filtrados)
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


def valores_columna_inventario(columna, q=''):
    """Valores distintos de una columna del Inventario en todos los grabados (respetando
    la búsqueda), para su filtro: {'valores': [...], 'etiquetas': {código: texto}}.
    Sin orden: lo ordena la pantalla (que junta None y '' en '—')."""
    if columna not in CAMPOS_INVENTARIO:
        raise FiltroConsultaInvalido(f'Columna desconocida: {columna}.')
    queryset = _filtrar_busqueda(Grabado.objects.all(), q)
    if columna in ('intentos', 'ultimo_k1'):
        queryset = _con_datos_k1(queryset)
    return {'valores': _valores_distintos(queryset, CAMPOS_INVENTARIO[columna]),
            'etiquetas': ETIQUETAS_INVENTARIO.get(columna, {})}


# ============================================================
# SOBRES
# ============================================================

LIMITE_SOBRES = 500


# Filtros por columna de la pantalla Sobres (mismo formato que el Inventario, ver
# _aplicar_filtros_consulta): columna de la pantalla -> campo de Sobre.
CAMPOS_SOBRES = {'of': 'of_origen', 'proceso': 'proceso', 'codigo': 'codigo', 'ubicacion': 'ubicacion',
                 'estado': 'estado'}
CAMPOS_FECHA_SOBRES = {'modificado': 'actualizado_el__date'}
ETIQUETAS_SOBRES = {'estado': dict(Sobre.ESTADO_CHOICES)}


def _filtrar_busqueda_sobres(queryset, q):
    for palabra in (q or '').split():
        queryset = queryset.filter(Q(of_origen__icontains=palabra) | Q(codigo__icontains=palabra)
                                   | Q(ubicacion__icontains=palabra))
    return queryset


def lista_sobres(q='', filtros=None):
    """Sobres para la pantalla Sobres: cada palabra de `q` tiene que aparecer
    en la OF de origen, el código o la ubicación, y se cumplen los `filtros` por
    columna (ver CAMPOS_SOBRES; uno inválido lanza FiltroConsultaInvalido). Hasta
    LIMITE_SOBRES, del modificado más reciente al más viejo; 'total' cuenta todos
    los que cumplen."""
    sobres = _filtrar_busqueda_sobres(Sobre.objects.all(), q)
    sobres = _aplicar_filtros_consulta(sobres, filtros, CAMPOS_SOBRES, CAMPOS_FECHA_SOBRES, set())
    total = sobres.count()
    estados_display = dict(Sobre.ESTADO_CHOICES)
    filas = sobres.select_related('desactivado_por').order_by('-actualizado_el', '-id')[:LIMITE_SOBRES]
    datos = [{
        'id': s.id,
        'of_origen': s.of_origen,
        'proceso': s.proceso,
        'codigo': s.codigo,
        'ubicacion': s.ubicacion,
        'estado': s.estado,
        'estado_display': estados_display.get(s.estado, s.estado),
        'motivo_desactivacion': s.motivo_desactivacion,
        'desactivado_por': _usuario(s.desactivado_por),
        'desactivado_el': formatear_fecha(s.desactivado_el),
        'actualizado_el': formatear_fecha(s.actualizado_el),
    } for s in filas]
    return {'total': total, 'limite': LIMITE_SOBRES, 'data': datos}


def valores_columna_sobres(columna, q=''):
    """Valores distintos de una columna de Sobres en todos los sobres (respetando la
    búsqueda), para su filtro: {'valores': [...], 'etiquetas': {código: texto}}."""
    if columna not in CAMPOS_SOBRES:
        raise FiltroConsultaInvalido(f'Columna desconocida: {columna}.')
    queryset = _filtrar_busqueda_sobres(Sobre.objects.all(), q)
    return {'valores': _valores_distintos(queryset, CAMPOS_SOBRES[columna]),
            'etiquetas': ETIQUETAS_SOBRES.get(columna, {})}


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
PLANI_INACTIVO = 'INACTIVO'                # desactivado: no se usa hasta que se reactive


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
    if grabado.estado == 'INACTIVO':
        return {'accion': PLANI_INACTIVO, 'grabado': resumen,
                'mensaje': 'Grabado inactivo: reactívalo para usarlo.'}
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


# ---------------------------------------------------------------- Tabla de Consultas

# Una sola fuente: las tablas nuevas (Grabado y EnvioMaquina). Dos tipos de fila en una lista:
# - ENVIO: una por cada EnvioMaquina (la OF del PLANI que se tiró), con los datos de su grabado;
# - CREACION: un grabado que todavía no se ha mandado a máquina (su OF es la de origen).
# OrdenFabricacion y LegadoOrden no se leen aquí: quedan de respaldo (las Estadísticas
# siguen leyendo OrdenFabricacion por api/registros).
FILA_ENVIO = 'ENVIO'
FILA_CREACION = 'CREACION'
ORIGEN_ANTERIOR = 'Sistema anterior'   # solo api/registros (Estadísticas, histórico)

LIMITE_CONSULTA = 500


class Constante:
    """Columna con el mismo valor en todas las filas de un tipo (None = vacía)."""
    def __init__(self, valor=None):
        self.valor = valor


SIN_DATO = Constante()

# Columna de la Tabla de Consultas -> campo de EnvioMaquina que se filtra.
CAMPOS_CONSULTA = {
    'of': 'of', 'of_origen': 'grabado__of_origen', 'cliente': 'grabado__cliente',
    'desc': 'grabado__referencia', 'proceso': 'grabado__proceso', 'estado': 'grabado__estado',
    'maquina': 'maquina__nombre', 'ubicacion': 'grabado__ubicacion', 'sobre': 'grabado__sobre',
    'cantidad': 'cantidad_formatos', 'horas': 'horas_proceso', 'papel': 'papel',
    'enviado_por': 'enviado_por__username', 'recogido_por': 'recogido_por__username',
    'estado_fisico': 'estado_fisico', 'tipo': 'grabado__tipo', 'usos': 'grabado__usos_acumulados',
}
CAMPOS_FECHA_CONSULTA = {'fecha': 'fecha_programada', 'modificado': 'grabado__actualizado_el__date',
                         'enviado_el': 'enviado_el__date', 'recogido_el': 'recogido_el__date'}

# Lo mismo para las filas de creación (Grabado sin envíos); lo que no tienen queda vacío.
CAMPOS_CONSULTA_CREACION = {
    **{columna: SIN_DATO for columna in CAMPOS_CONSULTA},
    'of': 'of_origen', 'of_origen': 'of_origen', 'cliente': 'cliente', 'desc': 'referencia',
    'proceso': 'proceso', 'estado': 'estado', 'ubicacion': 'ubicacion', 'sobre': 'sobre',
    'tipo': 'tipo', 'usos': 'usos_acumulados',
}
CAMPOS_FECHA_CONSULTA_CREACION = {**{columna: SIN_DATO for columna in CAMPOS_FECHA_CONSULTA},
                                  'modificado': 'actualizado_el__date'}

COLUMNAS_NUMERICAS_CONSULTA = {'cantidad', 'horas', 'usos'}
# Columnas que guardan un código: la pantalla muestra el texto pero filtra por el código.
ETIQUETAS_CONSULTA = {
    'estado': dict(Grabado.ESTADO_CHOICES), 'tipo': dict(Grabado.TIPO_CHOICES),
    'estado_fisico': dict(EnvioMaquina.ESTADO_FISICO_CHOICES),
}
VACIO_CONSULTA = '—'  # cómo llega en un filtro el valor vacío (null o '')
# SQL Server admite como mucho 2100 parámetros por consulta.
MAX_VALORES_FILTRO = 2000


class FiltroConsultaInvalido(ValueError):
    pass


def _envios_base(q=''):
    """EnvioMaquina con la búsqueda `q`: cada palabra en la OF del envío, la máquina o en
    la OF de origen, cliente, referencia, proceso, ubicación o estado de su grabado."""
    queryset = EnvioMaquina.objects.all()
    for palabra in (q or '').split():
        queryset = queryset.filter(
            Q(of__icontains=palabra) | Q(grabado__of_origen__icontains=palabra)
            | Q(grabado__cliente__icontains=palabra) | Q(grabado__referencia__icontains=palabra)
            | Q(grabado__proceso__icontains=palabra) | Q(grabado__ubicacion__icontains=palabra)
            | Q(grabado__estado__icontains=palabra) | Q(maquina__nombre__icontains=palabra)
        )
    return queryset


def _creaciones_base(q=''):
    """Grabados que nunca se han mandado a máquina, con la búsqueda `q` (cada palabra en
    OF de origen, cliente, referencia, proceso, ubicación o estado)."""
    queryset = Grabado.objects.exclude(id__in=EnvioMaquina.objects.values('grabado_id'))
    for palabra in (q or '').split():
        queryset = queryset.filter(
            Q(of_origen__icontains=palabra) | Q(cliente__icontains=palabra) | Q(referencia__icontains=palabra)
            | Q(proceso__icontains=palabra) | Q(ubicacion__icontains=palabra) | Q(estado__icontains=palabra)
        )
    return queryset


def _validar_valores(columna, valores, numericas=COLUMNAS_NUMERICAS_CONSULTA):
    if not isinstance(valores, list) or not all(isinstance(v, str) for v in valores):
        raise FiltroConsultaInvalido(f'Filtro inválido en {columna}.')
    if len(valores) > MAX_VALORES_FILTRO:
        raise FiltroConsultaInvalido('Demasiados valores marcados; usa la búsqueda para afinar.')
    if columna in numericas:
        try:
            return [v if v == VACIO_CONSULTA else float(v) for v in valores]
        except ValueError:
            raise FiltroConsultaInvalido(f'Valor no numérico en {columna}.')
    return valores


def _condicion_valores(columna, campo, valores, numericas=COLUMNAS_NUMERICAS_CONSULTA):
    """Q de las filas cuyo valor mostrado en `columna` está en `valores` (textos de pantalla)."""
    condicion = Q(pk__in=[])
    reales = [v for v in valores if v != VACIO_CONSULTA]
    if VACIO_CONSULTA in valores:
        condicion |= Q(**{f'{campo}__isnull': True})
        if columna not in numericas:
            condicion |= Q(**{campo: ''})
    if reales:
        condicion |= Q(**{f'{campo}__in': reales})
    return condicion


def _aplicar_filtros_consulta(queryset, filtros, campos=CAMPOS_CONSULTA, campos_fecha=CAMPOS_FECHA_CONSULTA,
                              numericas=COLUMNAS_NUMERICAS_CONSULTA):
    """`filtros` = lista de {'col': columna, 'in': [...]} | {'col', 'fuera': [...]} |
    {'col', 'desde': 'aaaa-mm-dd', 'hasta': ...}; se cumplen todos (una columna puede
    repetirse, ej. el filtro de Estado del panel y el de la columna).
    `campos`/`campos_fecha` traducen cada columna al campo del modelo o a una Constante;
    `numericas` son las columnas cuyos valores llegan como número (también la usa el Inventario)."""
    if not isinstance(filtros or [], list):
        raise FiltroConsultaInvalido('Filtros inválidos.')
    for filtro in filtros or []:
        if not isinstance(filtro, dict):
            raise FiltroConsultaInvalido('Filtros inválidos.')
        columna = filtro.get('col')
        if not isinstance(columna, str):
            raise FiltroConsultaInvalido('Filtros inválidos.')
        if columna in campos_fecha:
            campo = campos_fecha[columna]
            try:
                desde = date.fromisoformat(filtro['desde']) if filtro.get('desde') else None
                hasta = date.fromisoformat(filtro['hasta']) if filtro.get('hasta') else None
            except (TypeError, ValueError):
                raise FiltroConsultaInvalido(f'Fecha inválida en {columna}.')
            if isinstance(campo, Constante):
                # Sin esa fecha: igual que una fila vieja sin fecha, no pasa el filtro.
                if desde or hasta:
                    queryset = queryset.none()
                continue
            if desde:
                queryset = queryset.filter(**{f'{campo}__gte': desde})
            if hasta:
                queryset = queryset.filter(**{f'{campo}__lte': hasta})
            continue
        if columna not in campos:
            raise FiltroConsultaInvalido(f'Columna desconocida: {columna}.')
        incluir, excluir = filtro.get('in'), filtro.get('fuera')
        valores = _validar_valores(columna, incluir if incluir is not None else excluir, numericas)
        campo = campos[columna]
        if isinstance(campo, Constante):
            mostrado = campo.valor if campo.valor not in (None, '') else VACIO_CONSULTA
            if (mostrado in valores) != (incluir is not None):
                queryset = queryset.none()
            continue
        condicion = _condicion_valores(columna, campo, valores, numericas)
        queryset = queryset.filter(condicion) if incluir is not None else queryset.exclude(condicion)
    return queryset


def _of_origen_mostrada(grabado, envio):
    """OF de origen que muestra la celda: solo en un envío de otra OF (una repetición que
    apunta a su referencia, ej. la 23071 tirada con el grabado de la 7001). Misma OF, fila
    de creación o una OF que no se puede comparar: en blanco (mejor vacío que dudoso)."""
    if envio is None:
        return None
    of_envio, of_origen = normalizar_of(envio.of), normalizar_of(grabado.of_origen)
    if not of_envio or not of_origen or of_envio == of_origen:
        return None
    return grabado.of_origen


def _maquina_mostrada(envio):
    """Máquina que muestra la celda: en blanco si no hay envío o si es la máquina comodín
    de la migración (SIN MÁQUINA (LEGADO)); el filtro sigue usando el nombre real."""
    if envio is None or envio.maquina.nombre == MAQUINA_SIN_NOMBRE:
        return None
    return envio.maquina.nombre


def _fila_consulta(grabado, envio=None):
    """Fila de la tabla: un envío con los datos de su grabado, o la creación de un grabado
    sin envíos. Lo que no existe va en None (la pantalla lo deja en blanco).
    'of_origen' y 'maquina' son los valores reales (los usan búsqueda y filtros);
    'of_origen_mostrar' y 'maquina_mostrar', lo que se ve en la celda."""
    fila = {
        'id': f'e{envio.id}' if envio else f'g{grabado.id}',
        'tipo_fila': FILA_ENVIO if envio else FILA_CREACION,
        'of': envio.of if envio else grabado.of_origen,
        'of_origen': grabado.of_origen,
        'of_origen_mostrar': _of_origen_mostrada(grabado, envio),
        'cliente': grabado.cliente,
        'desc': grabado.referencia,
        'proceso': grabado.proceso,
        'estado': grabado.estado,
        'estado_display': grabado.get_estado_display(),
        'tipo': grabado.tipo,
        'tipo_display': grabado.get_tipo_display(),
        'ubicacion': grabado.ubicacion,
        'sobre': grabado.sobre,
        'usos': grabado.usos_acumulados,
        'modificado': formatear_fecha(grabado.actualizado_el),
        'maquina': None, 'maquina_mostrar': None, 'fecha': None, 'cantidad': None, 'horas': None, 'papel': None,
        'enviado_el': None, 'enviado_por': None, 'recogido_el': None, 'recogido_por': None,
        'estado_fisico': None, 'estado_fisico_display': None,
        'grabado': {'id': grabado.id, 'of_origen': grabado.of_origen, 'proceso': grabado.proceso,
                    'ubicacion': grabado.ubicacion, 'estado': grabado.estado,
                    'actualizado_el': formatear_fecha(grabado.actualizado_el)},
    }
    if envio:
        fila.update({
            'maquina': envio.maquina.nombre,
            'maquina_mostrar': _maquina_mostrada(envio),
            'fecha': envio.fecha_programada.strftime('%d/%m/%Y') if envio.fecha_programada else None,
            'cantidad': envio.cantidad_formatos,
            'horas': envio.horas_proceso,
            'papel': envio.papel,
            'enviado_el': formatear_fecha(envio.enviado_el),
            'enviado_por': _usuario(envio.enviado_por),
            'recogido_el': formatear_fecha(envio.recogido_el),
            'recogido_por': _usuario(envio.recogido_por),
            'estado_fisico': envio.estado_fisico,
            'estado_fisico_display': envio.get_estado_fisico_display() if envio.estado_fisico else None,
        })
    return fila


def consulta_registros(q='', filtros=None):
    """Filas de la Tabla de Consultas: una por cada EnvioMaquina y una por cada grabado
    sin envíos, con búsqueda y filtros sobre todo el historial. De la más reciente a la
    más vieja (fecha de envío o de creación del grabado), con tope LIMITE_CONSULTA.
    Devuelve {'total': cuántas cumplen, 'envios' y 'creaciones' (desglose del total),
    'limite', 'data': [filas]}. Un filtro inválido lanza FiltroConsultaInvalido."""
    envios = _aplicar_filtros_consulta(_envios_base(q), filtros)
    creaciones = _aplicar_filtros_consulta(_creaciones_base(q), filtros, CAMPOS_CONSULTA_CREACION,
                                           CAMPOS_FECHA_CONSULTA_CREACION)
    total_envios, total_creaciones = envios.count(), creaciones.count()

    # Las LIMITE_CONSULTA más recientes de la unión están entre las LIMITE_CONSULTA de cada tipo.
    filas = [(e.enviado_el, _fila_consulta(e.grabado, e))
             for e in envios.select_related('grabado', 'maquina', 'enviado_por', 'recogido_por')
             .order_by('-enviado_el', '-id')[:LIMITE_CONSULTA]]
    filas += [(g.creado_el, _fila_consulta(g))
              for g in creaciones.order_by('-creado_el', '-id')[:LIMITE_CONSULTA]]
    filas.sort(key=lambda fila: fila[0], reverse=True)  # estable: respeta el orden de cada tipo
    return {'total': total_envios + total_creaciones, 'envios': total_envios, 'creaciones': total_creaciones,
            'limite': LIMITE_CONSULTA, 'data': [fila for _, fila in filas[:LIMITE_CONSULTA]]}


def _valores_distintos(queryset, campo):
    if isinstance(campo, Constante):
        return [campo.valor] if queryset.exists() else []
    return list(queryset.order_by().values_list(campo, flat=True).distinct())


def valores_columna_consulta(columna, q=''):
    """Valores distintos de una columna en todos los envíos y grabados sin envío
    (respetando la búsqueda), para su filtro: {'valores': [...], 'etiquetas': {código: texto}}.
    Sin orden: lo ordena la pantalla (que junta None y '' en '—')."""
    if columna not in CAMPOS_CONSULTA:
        raise FiltroConsultaInvalido(f'Columna desconocida: {columna}.')
    valores = (_valores_distintos(_envios_base(q), CAMPOS_CONSULTA[columna])
               + _valores_distintos(_creaciones_base(q), CAMPOS_CONSULTA_CREACION[columna]))
    return {'valores': list(dict.fromkeys(valores)), 'etiquetas': ETIQUETAS_CONSULTA.get(columna, {})}


# ---------------------------------------------------------------- Tarjetas de la Tabla de Consultas

ESTADOS_PENDIENTES_GRABADO = ('EN_FABRICACION', 'PENDIENTE_K1')


def kpis_consulta(hoy):
    """Tarjetas de la Tabla de Consultas, solo desde Grabado y EnvioMaquina:
    - En máquina: grabados EN_MAQUINA (tarjeta "En máquina" del Inventario);
    - Terminados hoy: envíos recogidos hoy (OK o REPETIR);
    - Para repetir: grabados en REPETIR (tarjeta "Para repetir" del Inventario);
    - Pendientes: grabados en fabricación o pendientes de K1 (esas dos tarjetas del Inventario)."""
    por_estado = dict(Grabado.objects.values_list('estado').annotate(n=Count('id')).order_by())
    return {
        'en_maquina': por_estado.get('EN_MAQUINA', 0),
        'completados_hoy': EnvioMaquina.objects.filter(recogido_el__date=hoy).count(),
        'para_repetir': por_estado.get('REPETIR', 0),
        'pendientes': sum(por_estado.get(estado, 0) for estado in ESTADOS_PENDIENTES_GRABADO),
    }


# ------------------------------------------------------------------ etiqueta 6x4
LOGO_POR_DEFECTO = 'img/logo/Imagen1.png'
LETRA_PROCESO = {'STAMPING': 'S', 'EMBOSSING': 'E'}


def valor_codigo_barras(of_origen, proceso):
    """OF + letra del proceso: 22741-S (Stamping) / 22741-E (Embossing). No se guarda."""
    return f'{of_origen}-{LETRA_PROCESO.get(proceso, proceso[:1])}'


def configuracion_etiqueta():
    """
    Diseño vigente listo para dibujar: lista de campos (en orden, con su nombre) y
    la URL del logo (data URI del logo subido o el de static/). No escribe: si
    todavía no hay fila, usa los valores por defecto.
    """
    import base64

    from django.templatetags.static import static

    from .models import ConfiguracionEtiqueta
    from .services import etiquetas

    config = ConfiguracionEtiqueta.objects.filter(pk=1).first()
    try:
        campos = etiquetas.normalizar_campos(config.campos if config else None)
    except etiquetas.ConfiguracionInvalida:
        campos = etiquetas.normalizar_campos(None)   # una fila dañada no deja sin etiquetas
    if config and config.logo:
        logo_url = f'data:{config.logo_tipo};base64,{base64.b64encode(bytes(config.logo)).decode()}'
    else:
        logo_url = static(LOGO_POR_DEFECTO)
    return {
        'campos': [{'clave': clave, 'nombre': nombre, **campos[clave]}
                   for clave, nombre in etiquetas.CAMPOS_ETIQUETA],
        'campos_dict': campos,
        'logo_url': logo_url,
        'logo_propio': bool(config and config.logo),
    }


def _datos_etiqueta(*, of_origen, proceso, cliente, descripcion, ubicacion, fecha):
    from . import codigo128

    codigo = valor_codigo_barras(of_origen, proceso)
    return {
        'of': of_origen,
        'proceso': proceso,
        'cliente': cliente or '—',
        'descripcion': descripcion or '—',
        'ubicacion': ubicacion or '—',
        'fecha': formatear_fecha(fecha) or '—',
        'codigo': codigo,
        'codigo_svg': codigo128.svg(codigo),
    }


def datos_etiqueta_grabado(grabado_id):
    """Datos de la etiqueta de un grabado (su ubicación y su última modificación) o None."""
    grabado = Grabado.objects.filter(pk=grabado_id).first()
    if grabado is None:
        return None
    return _datos_etiqueta(of_origen=grabado.of_origen, proceso=grabado.proceso, cliente=grabado.cliente,
                           descripcion=grabado.referencia, ubicacion=grabado.ubicacion,
                           fecha=grabado.actualizado_el)


def datos_etiqueta_sobre(sobre_id):
    """
    Etiqueta de un sobre: ubicación y última modificación del SOBRE; cliente y
    descripción del grabado enlazado (si no tiene, "—"). None si no existe.
    """
    sobre = Sobre.objects.filter(pk=sobre_id).first()
    if sobre is None:
        return None
    grabado = Grabado.objects.filter(sobre_asociado=sobre).first()
    return _datos_etiqueta(of_origen=sobre.of_origen, proceso=sobre.proceso,
                           cliente=grabado.cliente if grabado else None,
                           descripcion=grabado.referencia if grabado else None,
                           ubicacion=sobre.ubicacion, fecha=sobre.actualizado_el)


def datos_etiqueta_prueba():
    """Datos de ejemplo para la página de prueba de impresión y el diseñador."""
    return _datos_etiqueta(of_origen='22741', proceso='STAMPING', cliente='CLIENTE DE EJEMPLO, S.A.',
                           descripcion='ANILLA DORADA 52 MM - EDICIÓN ESPECIAL CON RELIEVE',
                           ubicacion='GBB-2-1', fecha=timezone.now())


# ============================================================
# CONSULTOR DE OF (solo lectura)
# ============================================================

# Mismos valores que views._procesar_fila_externa toma como "acabado".
_ACABADO_SI = ('si', 'sí', 'true', '1', '1.0', 'ok', 's', 'y')


def _texto_externo(valor):
    """Texto del sistema externo sin espacios sobrantes; None si viene vacío (en pantalla, "—")."""
    texto = ' '.join(str(valor).split()) if valor is not None else ''
    return texto if texto not in ('', '—') else None


def _of_externa(valor, normalizar_of):
    """Una OF de la API como texto sin ".0" (22741.0 -> "22741"); si no es numérica, tal cual."""
    texto = _texto_externo(valor)
    if texto is None:
        return None
    of_int = normalizar_of(texto)
    return str(of_int) if of_int is not None else texto


def consultor_of(of, fila, normalizar_of):
    """
    Lo que muestra el Consultor de OF: los datos de la API para la OF (`fila` es
    views.consultar_of_externa, o None si la API no la tiene) y, por proceso, su
    OF de referencia, su acabado y el grabado de EIS que le toca. El grabado se
    resuelve como en el PLANI: con la OF de referencia del proceso (vacía -> la
    propia OF; sin fila en la API también la propia OF). No escribe nada.
    """
    fila = fila or {}
    # Forma mínima de buscar_datos_externos que necesitan of_origen_externa / proceso_ausente_en_externo.
    info = {'encontrado_ext': bool(fila),
            'of_stamping_ext': fila.get('OF_Stamping'), 'of_embossing_ext': fila.get('OF_Embossing')}
    campos = {'STAMPING': ('OF_Stamping', 'Acabat_Stamping'), 'EMBOSSING': ('OF_Embossing', 'Acabat_Embossing')}

    origenes = {p: of_origen_externa(info, p, normalizar_of) or of for p in PROCESOS_VALIDOS}
    grabados = {(g.of_origen, g.proceso): g
                for g in Grabado.objects.filter(of_origen__in=set(origenes.values()))}

    procesos = []
    for proceso in PROCESOS_VALIDOS:
        campo_of, campo_acabado = campos[proceso]
        acabado = _texto_externo(fila.get(campo_acabado))
        of_origen = origenes[proceso]
        grabado = grabados.get((of_origen, proceso))
        procesos.append({
            'proceso': proceso,
            'of_referencia': _of_externa(fila.get(campo_of), normalizar_of),
            'acabado': acabado,
            'terminada': acabado is not None and acabado.lower() in _ACABADO_SI,
            'of_origen': of_origen,
            'usa_grabado_de_otra': of_origen != of,
            'sin_proceso_en_api': proceso_ausente_en_externo(info, proceso, normalizar_of),
            'grabado': None if grabado is None else {
                'id': grabado.id,
                'of_origen': grabado.of_origen,
                'estado': grabado.estado,
                'estado_display': grabado.get_estado_display(),
                'tipo': grabado.tipo,
                'tipo_display': grabado.get_tipo_display(),
                'ubicacion': grabado.ubicacion or None,
            },
        })

    # OF de referencia general: la que trae la API si es una sola (un proceso, o los dos iguales).
    con_referencia = list({p['of_referencia'] for p in procesos if p['of_referencia']})
    return {
        'of': of,
        'encontrado': bool(fila),
        'datos': {
            'cliente': _texto_externo(fila.get('G_Cliente')),
            'descripcion': _texto_externo(fila.get('G_Referencia')),
            'sobre': _texto_externo(fila.get('Sobre_pelicula')),
            'of_referencia': con_referencia[0] if len(con_referencia) == 1 else None,
        },
        'procesos': procesos,
    }

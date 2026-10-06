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
from .services.grabados import es_auto_decision, es_autor_de_k1, puede_decidir_k1


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
        'aprobado_legado': grabado.aprobado_legado,
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


def inventario_grabados(q='', proceso='', estado=''):
    """Grabados para la pantalla de Inventario.
    - 'conteos': grabados por estado, respetando búsqueda y proceso (no el
      filtro de estado, para que las tarjetas muestren el reparto completo).
    - 'filas': hasta LIMITE_INVENTARIO grabados, del más nuevo al más viejo.
    - 'total': cuántos cumplen todos los filtros (puede superar el límite)."""
    base = _filtrar_busqueda(Grabado.objects.all(), q)
    if proceso:
        base = base.filter(proceso=proceso)

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
             .values('id', 'of_origen', 'proceso', 'cliente', 'referencia', 'estado',
                     'intentos_k1', 'ultimo_k1', 'usos_acumulados', 'ubicacion', 'creado_el')
             [:LIMITE_INVENTARIO])

    estados_display = dict(Grabado.ESTADO_CHOICES)
    datos = [{
        'id': f['id'],
        'of_origen': f['of_origen'],
        'proceso': f['proceso'],
        'cliente': f['cliente'],
        'referencia': f['referencia'],
        'estado': f['estado'],
        'estado_display': estados_display.get(f['estado'], f['estado']),
        'intentos_k1': f['intentos_k1'],
        'ultimo_k1': f['ultimo_k1'],
        'ultimo_k1_display': RESULTADOS_K1.get(f['ultimo_k1']),
        'usos_acumulados': f['usos_acumulados'],
        'ubicacion': f['ubicacion'],
        'creado_el': timezone.localtime(f['creado_el']).strftime('%d/%m/%Y') if f['creado_el'] else None,
    } for f in filas]

    return {'conteos': conteos, 'total': total, 'limite': LIMITE_INVENTARIO, 'data': datos}

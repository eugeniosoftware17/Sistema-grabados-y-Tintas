"""
Consultas de solo lectura sobre los modelos nuevos (grabado como entidad).
No escriben nada: arman diccionarios listos para JsonResponse, para que
cualquier pantalla (K1 Pendientes, más adelante la consulta de grabados)
reutilice el mismo formato.
"""
from django.db.models import Prefetch
from django.utils import timezone

from .models import EnvioMaquina, FabricacionGrabado, Grabado, PruebaK1

PERMISO_DECIDIR_K1 = 'gestion_grabados.decidir_pruebak1'


def formatear_fecha(valor):
    """dd/mm/aaaa hh:mm en la zona horaria de settings.TIME_ZONE (o None)."""
    if valor is None:
        return None
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


def _usuario(usuario):
    return usuario.username if usuario else None


def autores_de_k1(prueba):
    """Usuarios que no pueden decidir este K1: quien registró la fabricación
    probada y quien abrió el K1 (normalmente la misma persona)."""
    return {prueba.fabricacion.registrado_por_id, prueba.creado_por_id} - {None}


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
    'k1_actual' es el K1 pendiente (si hay), con la fabricación que prueba y si
    `usuario` puede decidirlo (tiene el permiso y no es autor de esa fabricación)."""
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
        es_propio = usuario.pk in autores_de_k1(pendiente)
        tiene_permiso = usuario.has_perm(PERMISO_DECIDIR_K1)
        k1_actual = _prueba(pendiente)
        k1_actual.update(
            fabricacion=_fabricacion(pendiente.fabricacion),
            es_propio=es_propio,
            puede_decidir=tiene_permiso and not es_propio,
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

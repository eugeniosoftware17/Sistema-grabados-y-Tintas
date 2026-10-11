"""
Reglas de negocio del sobre: el sobre físico con los diseños de creación de un
grabado (uno por grabado, misma OF de origen y proceso).

Todo lo que cambia un sobre pasa por aquí, dentro de transaction.atomic y con
select_for_update. El estado del sobre (ACTIVO / INACTIVO) no depende del del
grabado ni lo bloquea.
"""
import re

from django.db import transaction
from django.utils import timezone

from ..models import Grabado, HistorialActivacionSobre, HistorialUbicacionSobre, Sobre
from .grabados import (
    LARGO_MINIMO_MOTIVO_DESACTIVACION, ErrorGrabado, TransicionInvalida, _texto, validar_ubicacion,
)

ORIGEN_SOBRES = 'SOBRES'
ORIGEN_ALTA = 'ALTA'
ESTADO_ACTIVO = 'ACTIVO'
ESTADO_INACTIVO = 'INACTIVO'

# "10567.0" / "10567.00" -> "10567" (el Excel y la API traen el número como float).
_PATRON_DECIMAL_CERO = re.compile(r'^(\d+)\.0+$')


class SobreNoExiste(ErrorGrabado):
    status = 404


def limpiar_codigo_sobre(valor):
    """Código del sobre limpio: sin espacios sobrantes y sin decimales en cero.
    Cualquier otro texto ("Caja B2") queda como está. None / vacío -> ''.
    La migración 0026 lleva una copia congelada de esta regla."""
    codigo = ' '.join(_texto(valor).split())
    coincide = _PATRON_DECIMAL_CERO.match(codigo)
    return coincide.group(1) if coincide else codigo


def asegurar_sobre(grabado, *, usuario=None, ubicacion=None, origen=ORIGEN_ALTA):
    """Devuelve el sobre del grabado; si no tiene, toma el de su OF de origen y
    proceso o lo crea (código limpio a partir de grabado.sobre) y lo enlaza.
    El enlace se hace con update() para no tocar Grabado.actualizado_el.
    `ubicacion` (opcional) solo se usa al crear el sobre: se valida y deja
    historial. Hay que llamarla dentro de una transacción."""
    if grabado.sobre_asociado_id:
        return grabado.sobre_asociado
    ubicacion = validar_ubicacion(ubicacion) if _texto(ubicacion) else None

    sobre, creado = Sobre.objects.get_or_create(
        of_origen=grabado.of_origen, proceso=grabado.proceso,
        defaults={'codigo': limpiar_codigo_sobre(grabado.sobre), 'ubicacion': ubicacion,
                  'creado_por': usuario},
    )
    if creado and ubicacion:
        HistorialUbicacionSobre.objects.create(sobre=sobre, ubicacion_anterior=None, ubicacion_nueva=ubicacion,
                                               origen=origen, usuario=usuario)
    Grabado.objects.filter(pk=grabado.pk).update(sobre_asociado=sobre)
    grabado.sobre_asociado = sobre
    sobre.creado = creado
    return sobre


def _sobre_bloqueado(sobre_id):
    try:
        return Sobre.objects.select_for_update().get(pk=sobre_id)
    except (Sobre.DoesNotExist, ValueError, TypeError):
        raise SobreNoExiste('El sobre no existe.')


def cambiar_ubicacion_sobre(*, sobre_id, ubicacion, usuario, origen=ORIGEN_SOBRES):
    """Único camino para cambiar Sobre.ubicacion: valida el formato (GBB-2-1),
    normaliza y, si cambia, la guarda y deja la fila en HistorialUbicacionSobre.
    Si es la misma, no escribe nada. `sobre.ubicacion_cambio` dice si hubo cambio."""
    ubicacion = validar_ubicacion(ubicacion)
    with transaction.atomic():
        sobre = _sobre_bloqueado(sobre_id)
        if sobre.estado == ESTADO_INACTIVO:
            raise TransicionInvalida('El sobre está inactivo: reactívalo para cambiar su ubicación.')
        sobre.ubicacion_cambio = sobre.ubicacion != ubicacion
        if sobre.ubicacion_cambio:
            HistorialUbicacionSobre.objects.create(
                sobre=sobre, ubicacion_anterior=sobre.ubicacion, ubicacion_nueva=ubicacion,
                origen=origen, usuario=usuario,
            )
            sobre.ubicacion = ubicacion
            sobre.save(update_fields=['ubicacion', 'actualizado_el'])
    return sobre


def desactivar_sobre(*, sobre_id, motivo, usuario):
    """Pasa el sobre a INACTIVO con quién, cuándo y el motivo. No toca el grabado."""
    motivo = _texto(motivo)
    if len(motivo) < LARGO_MINIMO_MOTIVO_DESACTIVACION:
        raise ErrorGrabado(f'El motivo de la desactivación es obligatorio '
                           f'(al menos {LARGO_MINIMO_MOTIVO_DESACTIVACION} caracteres).')
    with transaction.atomic():
        sobre = _sobre_bloqueado(sobre_id)
        if sobre.estado == ESTADO_INACTIVO:
            raise TransicionInvalida('El sobre ya está inactivo.')
        sobre.estado = ESTADO_INACTIVO
        sobre.desactivado_por = usuario
        sobre.desactivado_el = timezone.now()
        sobre.motivo_desactivacion = motivo
        sobre.save(update_fields=['estado', 'desactivado_por', 'desactivado_el', 'motivo_desactivacion',
                                  'actualizado_el'])
        HistorialActivacionSobre.objects.create(sobre=sobre, accion='DESACTIVAR', motivo=motivo,
                                                usuario=usuario)
    return sobre


def reactivar_sobre(*, sobre_id, usuario):
    """Devuelve el sobre a ACTIVO y limpia los datos de la desactivación (el
    rastro queda en HistorialActivacionSobre)."""
    with transaction.atomic():
        sobre = _sobre_bloqueado(sobre_id)
        if sobre.estado != ESTADO_INACTIVO:
            raise TransicionInvalida('El sobre no está inactivo.')
        sobre.estado = ESTADO_ACTIVO
        sobre.desactivado_por = None
        sobre.desactivado_el = None
        sobre.motivo_desactivacion = ''
        sobre.save(update_fields=['estado', 'desactivado_por', 'desactivado_el', 'motivo_desactivacion',
                                  'actualizado_el'])
        HistorialActivacionSobre.objects.create(sobre=sobre, accion='REACTIVAR', usuario=usuario)
    return sobre

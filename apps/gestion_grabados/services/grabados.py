"""
Reglas de negocio del grabado como entidad propia (fase 2): alta / fabricación
de grabados y decisión de pruebas K1.

Las vistas (views_grabados.py) solo parsean el request y llaman a estas
funciones; todo lo que cambia estado pasa por acá, dentro de transaction.atomic
y con select_for_update sobre el grabado para que dos usuarios no pisen la
misma transición a la vez.
"""
import math

from django.db import IntegrityError, transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from ..models import (
    EstadoBano, FabricacionGrabado, Grabado, Maquina, OrdenFabricacion, PruebaK1,
)

PROCESOS_VALIDOS = ('STAMPING', 'EMBOSSING')

# Qué se puede hacer desde la pantalla de alta según el estado del grabado.
ACCION_ALTA = 'ALTA'                # no existe: Grabado + fabricación INICIAL + K1 intento 1
ACCION_RECHAZO_K1 = 'RECHAZO_K1'    # EN_FABRICACION: nueva fabricación + K1 intento n+1
ACCION_REPETICION = 'REPETICION'    # REPETIR: nueva fabricación, sin K1, vuelve a APROBADO
ACCION_BLOQUEADA = 'BLOQUEADA'

# Motivos de bloqueo (los usa el JS para decidir qué mostrar).
BLOQUEO_ESTADO = 'ESTADO'
BLOQUEO_HISTORIAL_LEGADO = 'HISTORIAL_LEGADO'
BLOQUEO_NO_ES_ORIGEN = 'NO_ES_ORIGEN'

LARGO_MINIMO_MOTIVO_RECHAZO = 5


class ErrorGrabado(Exception):
    """Error de negocio con mensaje para mostrar al usuario tal cual."""
    status = 400

    def __init__(self, mensaje):
        super().__init__(mensaje)
        self.mensaje = mensaje


class TransicionInvalida(ErrorGrabado):
    status = 409


class AutoDecisionProhibida(ErrorGrabado):
    status = 403


# ------------------------------------------------------------------ cálculos
def calcular_bano_ml(perdida):
    """Misma fórmula que el PLANI: Pérdida(g) / 1000 × 6.6 ml."""
    return (perdida / 1000) * 6.6 if perdida else 0


def compensacion_recomendada(perdida):
    """Redondeo igual a Math.round de JS (el round() de Python redondea al par)."""
    return math.floor(calcular_bano_ml(perdida) + 0.5)


def _texto(valor):
    return str(valor).strip() if valor is not None else ''


def _numero(datos, campo, etiqueta, obligatorio, entero=False):
    texto = _texto(datos.get(campo)).replace(',', '.')
    if not texto:
        if obligatorio:
            raise ErrorGrabado(f'El campo "{etiqueta}" es obligatorio.')
        return None
    try:
        numero = float(texto)
    except ValueError:
        raise ErrorGrabado(f'El campo "{etiqueta}" debe ser un número.')
    if not math.isfinite(numero) or numero < 0:
        raise ErrorGrabado(f'El campo "{etiqueta}" debe ser un número mayor o igual a 0.')
    if entero:
        if not numero.is_integer():
            raise ErrorGrabado(f'El campo "{etiqueta}" debe ser un número entero.')
        return int(numero)
    return numero


def validar_datos_tecnicos(datos):
    """Valida y convierte los datos técnicos de una fabricación.
    Obligatorios: responsables, peso inicial, peso final, temperatura y RPM; el
    motivo de compensación solo si la compensación difiere de la recomendada."""
    responsables = _texto(datos.get('responsables'))
    if not responsables:
        raise ErrorGrabado('El campo "Responsables" es obligatorio.')

    peso_inicial = _numero(datos, 'peso_inicial', 'Peso inicial', obligatorio=True)
    peso_final = _numero(datos, 'peso_final', 'Peso final', obligatorio=True)
    temp = _numero(datos, 'temp', 'Temperatura', obligatorio=True)
    rpm = _numero(datos, 'rpm', 'RPM', obligatorio=True, entero=True)
    tiempo = _numero(datos, 'tiempo', 'Tiempo real', obligatorio=False)

    perdida = max(0, peso_inicial - peso_final)

    compensacion_texto = _texto(datos.get('compensacion'))
    compensacion = _numero(datos, 'compensacion', 'Compensación', obligatorio=False)
    # Igual que verificarCambioCompensacion() del PLANI: parseInt(valor) || 0.
    compensacion_cambiada = int(compensacion or 0) != compensacion_recomendada(perdida)
    motivo = _texto(datos.get('compensacion_motivo'))
    if compensacion_cambiada and not motivo:
        raise ErrorGrabado('Cambiaste la compensación del valor recomendado: '
                           'explica el motivo antes de guardar.')

    return {
        'responsables': responsables,
        'peso_inicial': peso_inicial,
        'peso_final': peso_final,
        'perdida': perdida,
        'temp': temp,
        'rpm': rpm,
        'tiempo': _texto(datos.get('tiempo')) if tiempo is not None else None,
        'compensacion': compensacion_texto or None,
        'compensacion_motivo': motivo if compensacion_cambiada else None,
        'bano_ml': calcular_bano_ml(perdida),
    }


# --------------------------------------------------------------- evaluación
def _sin_dato(valor):
    return valor is None or _texto(valor) in ('', '—')


def of_origen_externa(info_externa, proceso, normalizar_of):
    """OF de origen del grabado para `proceso` según la API (OF Stamping / OF
    Embossing), normalizada. None si la API no la informa."""
    if not info_externa.get('encontrado_ext'):
        return None
    clave = 'of_stamping_ext' if proceso == 'STAMPING' else 'of_embossing_ext'
    valor = info_externa.get(clave)
    if _sin_dato(valor):
        return None
    of_int = normalizar_of(valor)
    return str(of_int) if of_int is not None else None


def tiene_historial_legado(of_origen, proceso):
    """Filas del sistema viejo (OrdenFabricacion) todavía no migradas que
    corresponden a este grabado: la propia OF o una OF que la usa como referencia."""
    return OrdenFabricacion.objects.filter(
        Q(of=of_origen) | Q(referencia=of_origen),
        proceso=proceso,
        legado__isnull=True,
    ).exists()


def _resumen_grabado(grabado):
    if grabado is None:
        return None
    ultimo_k1 = grabado.pruebas_k1.order_by('-intento').first()
    return {
        'estado': grabado.estado,
        'estado_display': grabado.get_estado_display(),
        'cliente': grabado.cliente,
        'referencia': grabado.referencia,
        'sobre': grabado.sobre or '',
        'ubicacion': grabado.ubicacion or '',
        'ultimo_intento_k1': ultimo_k1.intento if ultimo_k1 else 0,
        'ultimo_motivo_rechazo': (ultimo_k1.motivo_rechazo
                                  if ultimo_k1 and ultimo_k1.resultado == 'RECHAZADO' else None),
    }


def evaluar_alta(of_origen, proceso, info_externa, normalizar_of, grabado=None, buscar=True):
    """Decide qué permite la pantalla de alta para esta OF + proceso.
    Devuelve un dict con 'accion' (ALTA / RECHAZO_K1 / REPETICION / BLOQUEADA),
    y si está bloqueada, 'bloqueo', 'mensaje' y (si aplica) 'of_origen_sugerida'."""
    if buscar:
        grabado = Grabado.objects.filter(of_origen=of_origen, proceso=proceso).first()

    resultado = {'accion': ACCION_BLOQUEADA, 'grabado': _resumen_grabado(grabado)}

    if grabado is not None:
        if grabado.estado == 'EN_FABRICACION':
            resultado['accion'] = ACCION_RECHAZO_K1
        elif grabado.estado == 'REPETIR':
            resultado['accion'] = ACCION_REPETICION
        else:
            resultado.update(
                bloqueo=BLOQUEO_ESTADO,
                mensaje=f'Este grabado está en estado "{grabado.get_estado_display()}": '
                        'no se puede registrar una fabricación ahora.',
            )
        return resultado

    origen_api = of_origen_externa(info_externa, proceso, normalizar_of)
    if origen_api and origen_api != of_origen:
        resultado.update(
            bloqueo=BLOQUEO_NO_ES_ORIGEN,
            mensaje=f'La OF {of_origen} usa el grabado de la OF {origen_api} para {proceso}: '
                    'no es una OF de origen.',
            of_origen_sugerida=origen_api,
        )
        return resultado

    if tiene_historial_legado(of_origen, proceso):
        resultado.update(
            bloqueo=BLOQUEO_HISTORIAL_LEGADO,
            mensaje='Este grabado tiene historial en el sistema anterior; '
                    'se incorporará con la migración.',
        )
        return resultado

    resultado['accion'] = ACCION_ALTA
    return resultado


# ------------------------------------------------------------------ escritura
def _sumar_bano(bano_ml):
    """Suma al contador único del baño sin pisar sumas concurrentes."""
    if bano_ml <= 0:
        return None
    estado_bano = EstadoBano.obtener()
    EstadoBano.objects.filter(pk=estado_bano.pk).update(ml_acumulados=F('ml_acumulados') + bano_ml)
    estado_bano.refresh_from_db()
    return {
        'ml_acumulados': round(estado_bano.ml_acumulados, 1),
        'limite': EstadoBano.LIMITE_ML,
        'alerta': estado_bano.ml_acumulados >= EstadoBano.LIMITE_ML,
    }


def _maquina_activa(maquina_id):
    if not maquina_id:
        raise ErrorGrabado('Hay que elegir la máquina del K1.')
    try:
        return Maquina.objects.get(pk=maquina_id, activa=True)
    except (Maquina.DoesNotExist, ValueError, TypeError):
        raise ErrorGrabado('La máquina elegida no existe o no está activa.')


def datos_externos_utiles(info_externa):
    """Cliente / referencia / sobre que la API trae con valor (vacíos si la OF
    no se encontró o si su fila en CigarRings2012 tiene esos campos en NULL)."""
    encontrado = info_externa.get('encontrado_ext')

    def valor(clave):
        return '' if not encontrado or _sin_dato(info_externa.get(clave)) else _texto(info_externa[clave])

    return {
        'cliente': valor('cliente_ext'),
        'referencia': valor('descripcion_ext'),
        'sobre': valor('sobre_ext'),
    }


def _datos_grabado_nuevo(info_externa, datos_manuales):
    """Cliente / referencia / sobre: lo que traiga la API con valor; lo que
    falte se toma de lo escrito a mano (cliente y referencia obligatorios) y en
    ese caso se marca datos_manuales=True."""
    externos = datos_externos_utiles(info_externa)
    datos = {}
    usa_manuales = False
    for campo in ('cliente', 'referencia', 'sobre'):
        if externos[campo]:
            datos[campo] = externos[campo]
        else:
            datos[campo] = _texto(datos_manuales.get(campo))
            usa_manuales = usa_manuales or bool(datos[campo]) or campo != 'sobre'

    if not datos['cliente'] or not datos['referencia']:
        raise ErrorGrabado('El sistema externo no tiene cliente y referencia para esta OF: '
                           'hay que escribirlos a mano.')
    datos['sobre'] = datos['sobre'] or None
    datos['datos_manuales'] = usa_manuales
    return datos


def registrar_fabricacion(*, of_origen, proceso, info_externa, normalizar_of,
                          datos_tecnicos, maquina_id, datos_manuales, usuario):
    """Registra una fabricación desde la pantalla de alta. El caso (alta nueva,
    refabricación por K1 rechazado o por REPETIR) lo decide el estado real del
    grabado, no lo que mande el navegador.
    Devuelve {'accion', 'grabado', 'fabricacion', 'prueba_k1', 'bano'}."""
    if proceso not in PROCESOS_VALIDOS:
        raise ErrorGrabado('Proceso inválido.')
    tecnicos = validar_datos_tecnicos(datos_tecnicos)

    try:
        with transaction.atomic():
            grabado = (Grabado.objects.select_for_update()
                       .filter(of_origen=of_origen, proceso=proceso).first())
            evaluacion = evaluar_alta(of_origen, proceso, info_externa, normalizar_of,
                                      grabado=grabado, buscar=False)
            accion = evaluacion['accion']
            if accion == ACCION_BLOQUEADA:
                raise TransicionInvalida(evaluacion['mensaje'])

            maquina = _maquina_activa(maquina_id) if accion != ACCION_REPETICION else None

            if accion == ACCION_ALTA:
                grabado = Grabado.objects.create(
                    of_origen=of_origen, proceso=proceso, creado_por=usuario,
                    estado='PENDIENTE_K1',
                    **_datos_grabado_nuevo(info_externa, datos_manuales),
                )
                tipo, numero = 'INICIAL', 1
            else:
                ultimo = grabado.fabricaciones.aggregate(n=Max('numero'))['n'] or 0
                tipo = 'RECHAZO_K1' if accion == ACCION_RECHAZO_K1 else 'REPETICION'
                numero = ultimo + 1
                grabado.estado = 'PENDIENTE_K1' if accion == ACCION_RECHAZO_K1 else 'APROBADO'
                grabado.save(update_fields=['estado', 'actualizado_el'])

            fabricacion = FabricacionGrabado.objects.create(
                grabado=grabado, numero=numero, tipo=tipo, registrado_por=usuario, **tecnicos,
            )

            prueba = None
            if accion != ACCION_REPETICION:
                ultimo_intento = grabado.pruebas_k1.aggregate(n=Max('intento'))['n'] or 0
                prueba = PruebaK1.objects.create(
                    grabado=grabado, fabricacion=fabricacion, intento=ultimo_intento + 1,
                    maquina=maquina, creado_por=usuario,
                )

            bano = _sumar_bano(tecnicos['bano_ml'])
    except IntegrityError:
        # Dos altas simultáneas de la misma OF + proceso: gana la primera.
        raise TransicionInvalida('Otro usuario acaba de registrar este grabado. '
                                 'Vuelve a buscar la OF para ver su estado actual.')

    return {'accion': accion, 'grabado': grabado, 'fabricacion': fabricacion,
            'prueba_k1': prueba, 'bano': bano}


def decidir_k1(*, prueba_id, usuario, aprobar, motivo=None):
    """Aprueba o rechaza un K1 pendiente. Quien registró la fabricación probada
    no puede decidir sobre su propio K1, aunque sea supervisor o superusuario."""
    motivo = _texto(motivo)
    if not aprobar and len(motivo) < LARGO_MINIMO_MOTIVO_RECHAZO:
        raise ErrorGrabado(f'El motivo del rechazo es obligatorio '
                           f'(al menos {LARGO_MINIMO_MOTIVO_RECHAZO} caracteres).')

    with transaction.atomic():
        try:
            prueba = (PruebaK1.objects.select_for_update()
                      .select_related('fabricacion').get(pk=prueba_id))
        except PruebaK1.DoesNotExist:
            raise ErrorGrabado('El K1 no existe.')
        grabado = Grabado.objects.select_for_update().get(pk=prueba.grabado_id)

        if prueba.resultado != 'PENDIENTE':
            raise TransicionInvalida(f'Este K1 ya fue {prueba.get_resultado_display().lower()}.')
        if grabado.estado != 'PENDIENTE_K1':
            raise TransicionInvalida(f'El grabado está en estado "{grabado.get_estado_display()}", '
                                     'no en "Pendiente de K1".')
        autores = {prueba.fabricacion.registrado_por_id, prueba.creado_por_id} - {None}
        if usuario.pk in autores:
            raise AutoDecisionProhibida('No puedes aprobar ni rechazar el K1 de una fabricación '
                                        'que registraste tú: tiene que decidirlo otro supervisor.')

        prueba.resultado = 'APROBADO' if aprobar else 'RECHAZADO'
        prueba.motivo_rechazo = None if aprobar else motivo
        prueba.decidido_por = usuario
        prueba.decidido_el = timezone.now()
        prueba.save(update_fields=['resultado', 'motivo_rechazo', 'decidido_por', 'decidido_el'])

        grabado.estado = 'APROBADO' if aprobar else 'EN_FABRICACION'
        grabado.save(update_fields=['estado', 'actualizado_el'])

    return prueba

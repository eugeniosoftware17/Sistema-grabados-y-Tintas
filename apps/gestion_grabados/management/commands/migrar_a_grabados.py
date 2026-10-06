"""
Migración de datos: pasa OrdenFabricacion (sistema viejo) a los modelos nuevos
Grabado / FabricacionGrabado / EnvioMaquina / Maquina, y deja cada fila
registrada una sola vez en LegadoOrden (conciliación).

Seguridad:
- Por defecto es una SIMULACIÓN: corre todo dentro de una transacción y la
  revierte al final. Solo guarda con --aplicar.
- Una sola transacción; si LegadoOrden.count() != OrdenFabricacion.count() al
  final, se revierte todo.
- Idempotente: se salta las filas que ya tienen LegadoOrden.
- OrdenFabricacion no se modifica nunca.
- Si la API externa no responde, se detiene antes de abrir la transacción.

El reporte sale en pantalla y se guarda completo en
reportes_migracion/migrar_a_grabados_<fecha>_<hora>.txt.

Uso:
    python manage.py migrar_a_grabados                 # simulación
    python manage.py migrar_a_grabados --limite 0      # simulación, listas completas en pantalla
    python manage.py migrar_a_grabados --aplicar       # guarda
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.gestion_grabados.management.commands.auditar_grabados import (
    CAMPOS_TECNICOS, normalizar_of, referencia_valida,
)
from apps.gestion_grabados.models import (
    EnvioMaquina, FabricacionGrabado, Grabado, LegadoOrden, Maquina, OrdenFabricacion,
)
from apps.gestion_grabados.services.grabados import (
    PROCESOS_VALIDOS, calcular_bano_ml, datos_externos_utiles, normalizar_nombre_maquina,
)

# Estado del grabado según el estado de la fila legada más reciente (regla 3).
ESTADO_GRABADO = {
    'COMPLETADO': 'APROBADO',
    'EN_PROCESO': 'APROBADO',
    'REPETIR': 'REPETIR',
    'REVISION': 'REPETIR',
    'EN_MAQUINA': 'EN_MAQUINA',
}
# Filas que se convierten en un envío cerrado, con su estado físico (regla 6).
ESTADO_FISICO_ENVIO = {'COMPLETADO': 'OK', 'REPETIR': 'REPETIR', 'REVISION': 'REPETIR'}
ESTADOS_REPETIR = ('REPETIR', 'REVISION')

PREFIJOS_EVENTO = ('FALLO:', 'Físico:')
CAMPOS_PESO = ('peso_inicial', 'peso_final', 'perdida')
FECHA_CORTE_PESOS = date(2026, 8, 12)
MAQUINA_SIN_NOMBRE = 'SIN MÁQUINA (LEGADO)'
# Nombres que todavía no se sabe a qué máquina corresponden: se listan sus filas.
MAQUINAS_POR_REVISAR = ('GBB-1-3',)
# SQL Server admite como mucho 2100 parámetros por consulta.
TAMANO_LOTE_API = 500


class ConciliacionFallida(Exception):
    pass


def consultar_externos(ofs, proceso):
    """Cliente / referencia / sobre de la API para las OF de origen, en lotes.
    A diferencia de views.buscar_datos_externos_batch, NO se traga los errores:
    si la API (o externa_2012) no responde, la excepción sube y la migración se
    detiene. Devuelve {of_origen: info} solo con las OF encontradas."""
    from apps.gestion_grabados.views import (
        _buscar_filas_api_batch, _buscar_filas_db_batch, _procesar_fila_externa,
    )
    buscar = (_buscar_filas_api_batch if getattr(settings, 'EXTERNA_2012_SOURCE', 'db') == 'api'
              else _buscar_filas_db_batch)
    of_ints = sorted({int(of) for of in ofs})
    resultado = {}
    for i in range(0, len(of_ints), TAMANO_LOTE_API):
        for g_orden, fila in buscar(of_ints[i:i + TAMANO_LOTE_API]).items():
            resultado[str(int(g_orden))] = _procesar_fila_externa(fila, proceso)
    return resultado


def es_evento(texto):
    return (texto or '').startswith(PREFIJOS_EVENTO)


def fecha_local(valor):
    return timezone.localtime(valor).date() if timezone.is_aware(valor) else valor.date()


def tiene_valor(valor):
    return valor is not None and str(valor).strip() != ''


class Reporte:
    """Junta las líneas del reporte: en pantalla las listas se recortan a
    `limite`; en el archivo van completas."""

    def __init__(self, comando, limite):
        self.comando = comando
        self.limite = limite
        self.archivo = []

    def _pantalla(self, texto, estilo=None):
        self.comando.stdout.write(estilo(texto) if estilo else texto)

    def titulo(self, texto):
        for linea in ('', '=' * 78, texto, '=' * 78):
            self.archivo.append(linea)
            self._pantalla(linea, self.comando.style.MIGRATE_HEADING if linea else None)

    def linea(self, texto='', estilo=None):
        self.archivo.append(texto)
        self._pantalla(texto, estilo)

    def conteo(self, etiqueta, valor, alerta=False):
        texto = f'  {etiqueta:<60} {valor:>7}'
        self.linea(texto, self.comando.style.WARNING if alerta and valor else None)

    def lista(self, items):
        items = list(items)
        self.archivo.extend('    ' + item for item in items)
        mostrar = items if self.limite == 0 else items[:self.limite]
        for item in mostrar:
            self._pantalla('    ' + item)
        if len(mostrar) < len(items):
            self._pantalla(f'    ... y {len(items) - len(mostrar)} más (completo en el archivo del reporte)')

    def guardar(self, carpeta):
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f'migrar_a_grabados_{timezone.localtime():%Y%m%d_%H%M%S}.txt'
        ruta.write_text('\n'.join(self.archivo) + '\n', encoding='utf-8')
        return ruta


class Command(BaseCommand):
    help = ('Migra OrdenFabricacion a Grabado / FabricacionGrabado / EnvioMaquina. '
            'Por defecto solo simula; guarda con --aplicar.')

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Guardar los cambios. Sin esta opción es una simulación.')
        parser.add_argument('--limite', type=int, default=20,
                            help='Máximo de casos por lista en pantalla (0 = todos). '
                                 'El archivo del reporte siempre lleva las listas completas.')
        parser.add_argument('--salida', default=None,
                            help='Carpeta del archivo del reporte (por defecto reportes_migracion/).')

    # ================================================================ entrada
    def handle(self, *args, **opts):
        self.aplicar = opts['aplicar']
        self.r = Reporte(self, opts['limite'])
        carpeta = Path(opts['salida']) if opts['salida'] else Path(settings.BASE_DIR) / 'reportes_migracion'

        db = settings.DATABASES['default']
        modo = 'APLICAR (guarda los cambios)' if self.aplicar else 'SIMULACIÓN (no guarda nada)'
        self.r.titulo(f'MIGRAR A GRABADOS — {modo}')
        self.r.linea(f"  Base: {db.get('NAME')} @ {db.get('HOST')}")
        self.r.linea(f'  Fecha: {timezone.localtime():%d/%m/%Y %H:%M:%S}')

        error = None
        try:
            self.ejecutar()
        except CommandError as e:
            error = e
            self.r.linea('')
            self.r.linea(f'  ERROR: {e}', self.style.ERROR)
        finally:
            ruta = self.r.guardar(carpeta)
            self.stdout.write(f'\nReporte guardado en {ruta}')
        if error:
            raise error

    def ejecutar(self):
        self.cargar_filas()
        self.seccion_pendientes()
        self.seccion_cadenas()
        self.seccion_maquinas()
        self.externos = self.consultar_api()
        self.seccion_pesos()

        try:
            with transaction.atomic():
                self.migrar()
                self.seccion_repeticiones()
                self.seccion_conflictos()
                self.seccion_conteos_finales()
                if not self.aplicar:
                    transaction.set_rollback(True)
        except ConciliacionFallida as e:
            raise CommandError(f'{e} Se revirtió todo: no se guardó nada.')

        self.r.linea('')
        if self.aplicar:
            self.r.linea('  Migración guardada.', self.style.SUCCESS)
        else:
            self.r.linea('  SIMULACIÓN: no se guardó nada. Usa --aplicar para guardar.',
                         self.style.SUCCESS)

    # ================================================================ lectura
    def cargar_filas(self):
        """Filas de OrdenFabricacion todavía sin LegadoOrden, agrupadas por
        (OF Referencia o la propia OF, proceso). La referencia se toma literal."""
        self.total_ordenes = OrdenFabricacion.objects.count()
        self.filas = list(OrdenFabricacion.objects.filter(legado__isnull=True).order_by('id').values())
        self.ya_migradas = self.total_ordenes - len(self.filas)

        self.grupos = defaultdict(list)
        self.sin_grupo = []          # OF sin dígitos o proceso inválido: SIN_MIGRAR sin grabado
        for f in self.filas:
            f['of_norm'] = normalizar_of(f['of'])
            f['ref_norm'] = referencia_valida(f)
            f['tecnicos'] = any(tiene_valor(f[c]) for c in CAMPOS_TECNICOS)
            f['migrable'] = f['estado'] in ESTADO_GRABADO
            if not f['of_norm'] or f['proceso'] not in PROCESOS_VALIDOS:
                self.sin_grupo.append(f)
                continue
            f['clave'] = (f['ref_norm'] or f['of_norm'], f['proceso'])
            self.grupos[f['clave']].append(f)
        for filas in self.grupos.values():
            filas.sort(key=lambda f: (f['creado_el'], f['id']))

        self.r.titulo('0. ALCANCE')
        self.r.conteo('Filas en OrdenFabricacion', self.total_ordenes)
        self.r.conteo('  ya migradas antes (tienen LegadoOrden, se saltan)', self.ya_migradas)
        self.r.conteo('  a procesar en esta corrida', len(self.filas))
        self.r.conteo('Grupos (OF de origen, proceso)', len(self.grupos))
        self.r.conteo('Filas sin OF válida o con proceso inválido (SIN_MIGRAR)', len(self.sin_grupo), alerta=True)
        self.r.lista(self.desc(f) for f in self.sin_grupo)
        otras = [f for f in self.filas if not f['migrable'] and f['estado'] != 'PENDIENTE']
        self.r.conteo('Filas en otro estado no migrable (ej. CANCELADO, SIN_MIGRAR)', len(otras), alerta=True)
        self.r.lista(self.desc(f) for f in otras)

    @staticmethod
    def desc(f):
        return (f"id={f['id']} OF={f['of']} {f['proceso']} {f['estado']}"
                + (' [manual]' if f['origen_manual'] else ''))

    def consultar_api(self):
        """{(of_origen, proceso): info} de la API para los grupos que crearán un
        Grabado nuevo. Si la API falla, se detiene sin abrir la transacción."""
        existentes = set(Grabado.objects.values_list('of_origen', 'proceso'))
        por_proceso = defaultdict(set)
        for clave, filas in self.grupos.items():
            if clave not in existentes and any(f['migrable'] for f in filas):
                por_proceso[clave[1]].add(clave[0])

        externos = {}
        no_encontradas = []
        for proceso in PROCESOS_VALIDOS:
            ofs = sorted(por_proceso[proceso], key=int)
            if not ofs:
                continue
            try:
                info = consultar_externos(ofs, proceso)
            except Exception as e:
                raise CommandError(f'La API externa no respondió al consultar {len(ofs)} OF de {proceso} '
                                   f'({type(e).__name__}: {e}). No se guardó nada.')
            for of in ofs:
                if of in info:
                    externos[(of, proceso)] = info[of]
                else:
                    no_encontradas.append((of, proceso))

        self.r.titulo('4. OF DE ORIGEN QUE LA API NO ENCUENTRA')
        self.r.linea(f"  Origen configurado: {getattr(settings, 'EXTERNA_2012_SOURCE', 'db')}")
        self.r.conteo('OF de origen consultadas', sum(len(v) for v in por_proceso.values()))
        self.r.conteo('No encontradas (se usan los datos de la fila legada, datos_manuales)',
                      len(no_encontradas), alerta=True)
        self.r.lista(f'OF {of} {proceso}' for of, proceso in no_encontradas)
        return externos

    # ========================================================= secciones previas
    def seccion_pendientes(self):
        pendientes = [f for f in self.filas if f['estado'] == 'PENDIENTE']
        self.r.titulo('1. FILAS PENDIENTE (no se migran: quedan como SIN_MIGRAR)')
        self.r.conteo('Total', len(pendientes))
        conteo = Counter(('Fabricación (manual)' if f['origen_manual'] else 'PLANI',
                          'con datos técnicos' if f['tecnicos'] else 'sin datos técnicos')
                         for f in pendientes)
        for (origen, tecnicos), n in sorted(conteo.items()):
            self.r.conteo(f'  {origen:<22} {tecnicos}', n)
        self.r.lista(self.desc(f) + (' [con técnicos]' if f['tecnicos'] else '')
                     for f in pendientes)

    def seccion_cadenas(self):
        """Filas cuya OF Referencia es a su vez una fila con otra referencia. No se
        siguen (la referencia se toma literal), solo se reportan."""
        por_clave = {(f['of_norm'], f['proceso']): f for f in self.filas if f['of_norm']}
        cadenas = []
        for f in self.filas:
            if not f['ref_norm']:
                continue
            referida = por_clave.get((f['ref_norm'], f['proceso']))
            if referida and referida['ref_norm']:
                cadenas.append(f"{self.desc(f)}: {f['of_norm']} -> {f['ref_norm']} -> {referida['ref_norm']}")
        self.r.titulo('2. CADENAS DE REFERENCIAS (no se siguen; el grabado queda en la primera)')
        self.r.conteo('Filas con referencia en cadena', len(cadenas), alerta=True)
        self.r.lista(cadenas)

    def seccion_maquinas(self):
        catalogo = {normalizar_nombre_maquina(m.nombre) for m in Maquina.objects.all()}
        por_forma = defaultdict(Counter)
        for f in self.filas:
            por_forma[normalizar_nombre_maquina(f['maquina'])][f['maquina'] or ''] += 1

        self.r.titulo('3. NOMBRES DE MÁQUINA (agrupados por forma normalizada)')
        sin_maquina = sum(por_forma.pop('', Counter()).values())
        duplicadas = sum(len(v) > 1 for v in por_forma.values())
        self.r.conteo('Formas normalizadas distintas', len(por_forma))
        self.r.conteo('  con más de una escritura (se unifican)', duplicadas, alerta=True)
        self.r.conteo(f'Filas sin máquina (sus envíos van a "{MAQUINA_SIN_NOMBRE}")', sin_maquina, alerta=True)
        self.r.lista(
            f"{forma}: {sum(nombres.values())} "
            f"[{'en catálogo' if forma in catalogo else 'se crea'}] — "
            + ', '.join(f'{nombre!r} x{n}' for nombre, n in nombres.most_common())
            for forma, nombres in sorted(por_forma.items())
        )

        por_revisar = [f for f in self.filas
                       if normalizar_nombre_maquina(f['maquina']) in MAQUINAS_POR_REVISAR]
        self.r.conteo(f"Filas con máquina por identificar ({', '.join(MAQUINAS_POR_REVISAR)}; no se unifica)",
                      len(por_revisar), alerta=True)
        self.r.lista(f"id={f['id']} OF={f['of']} {f['proceso']} {f['estado']} máquina={f['maquina']!r}"
                     for f in por_revisar)

    def seccion_pesos(self):
        filas = [f for f in self.filas
                 if fecha_local(f['creado_el']) < FECHA_CORTE_PESOS
                 and any(f[c] is not None for c in CAMPOS_PESO)]
        self.r.titulo(f'5. FILAS CON PESOS ANTERIORES AL {FECHA_CORTE_PESOS:%d/%m/%Y} '
                      '(se copian tal cual y se marcan para revisión)')
        self.r.conteo('Filas', len(filas), alerta=True)
        self.r.lista(f"{self.desc(f)} creada {fecha_local(f['creado_el']):%d/%m/%Y} "
                     f"inicial={f['peso_inicial']} final={f['peso_final']} pérdida={f['perdida']}"
                     for f in filas)

    # ================================================================ escritura
    def migrar(self):
        self.maquinas = {}
        for maquina in Maquina.objects.order_by('id'):
            self.maquinas.setdefault(normalizar_nombre_maquina(maquina.nombre), maquina)
        self.maquinas_creadas = []
        self.conflictos = []
        self.repeticiones = []
        self.roles = Counter()
        self.grabados_nuevos = 0
        self.grabados_reutilizados = 0
        # Los grabados que ya estaban no cambian de estado (los reutilizados solo suman usos).
        self.estados_previos = Counter(Grabado.objects.values_list('estado', flat=True))
        self.estados_nuevos = Counter()
        self.fabricaciones = Counter()
        self.envios = Counter()

        for f in self.sin_grupo:
            self.enlazar(f, 'SIN_MIGRAR')
        for clave in sorted(self.grupos, key=lambda c: (c[1], int(c[0]))):
            self.migrar_grupo(clave, self.grupos[clave])

        legados = LegadoOrden.objects.count()
        ordenes = OrdenFabricacion.objects.count()
        self.conciliacion = (legados, ordenes)
        if legados != ordenes:
            self.seccion_conteos_finales()
            raise ConciliacionFallida(f'La conciliación no cuadra: LegadoOrden={legados}, '
                                      f'OrdenFabricacion={ordenes}.')

    def enlazar(self, f, rol, grabado=None, fabricacion=None, envio=None):
        LegadoOrden.objects.create(orden_id=f['id'], rol=rol, grabado=grabado,
                                   fabricacion=fabricacion, envio=envio)
        self.roles[rol] += 1

    def migrar_grupo(self, clave, filas):
        of_origen, proceso = clave
        grabado = Grabado.objects.select_for_update().filter(of_origen=of_origen, proceso=proceso).first()
        migrables = [f for f in filas if f['migrable']]
        if not migrables:
            # Solo PENDIENTE: no hay grabado que crear.
            for f in filas:
                self.enlazar(f, 'SIN_MIGRAR', grabado)
            return

        vigente = max(migrables, key=lambda f: (f['actualizado_el'], f['id']))
        estado = ESTADO_GRABADO[vigente['estado']]
        usos = sum(f['usos_acumulados'] or 0 for f in migrables)
        reutilizado = grabado is not None

        if reutilizado:
            self.grabados_reutilizados += 1
            if grabado.estado != estado:
                self.conflictos.append(
                    f'{of_origen} {proceso}: el grabado de Alta está en {grabado.estado} y las filas '
                    f'legadas dicen {estado} (fila id={vigente["id"]} {vigente["estado"]}); se deja '
                    f'{grabado.estado}.')
            numero = grabado.fabricaciones.aggregate(n=Max('numero'))['n'] or 0
            if numero:
                self.conflictos.append(f'{of_origen} {proceso}: ya tenía {numero} fabricación(es) en EIS; '
                                       'las legadas se numeran a continuación.')
            Grabado.objects.filter(pk=grabado.pk).update(usos_acumulados=F('usos_acumulados') + usos)
        else:
            self.grabados_nuevos += 1
            self.estados_nuevos[estado] += 1
            grabado = Grabado.objects.create(
                of_origen=of_origen, proceso=proceso, estado=estado, aprobado_legado=True,
                ubicacion=self.ultimo_valor(migrables, 'ubicacion', 200),
                usos_acumulados=usos, creado_por_id=migrables[0]['usuario_id'],
                **self.datos_grabado(clave, migrables),
            )
            # creado_el es auto_now_add: se corrige a la fecha de la primera fila legada.
            Grabado.objects.filter(pk=grabado.pk).update(creado_el=migrables[0]['creado_el'])
            numero = 0

        hay_inicial = False
        pendiente_repeticion = False
        for f in filas:
            if not f['migrable']:
                self.enlazar(f, 'SIN_MIGRAR', grabado)
                continue

            fabricacion = None
            if not hay_inicial and f['tecnicos']:
                numero += 1
                fabricacion = self.crear_fabricacion(grabado, f, numero, 'INICIAL')
                hay_inicial, pendiente_repeticion = True, False
            elif hay_inicial and pendiente_repeticion:
                numero += 1
                fabricacion = self.crear_fabricacion(grabado, f, numero, 'REPETICION')
                pendiente_repeticion = False
                self.repeticiones.append(f'{self.desc(f)} -> grabado {of_origen} {proceso}, '
                                         f'fabricación {numero}')
            if f['estado'] in ESTADOS_REPETIR:
                pendiente_repeticion = True

            abierto = f is vigente and f['estado'] == 'EN_MAQUINA' and not reutilizado
            envio = self.crear_envio(grabado, f, abierto)

            if fabricacion:
                rol = 'FABRICACION'
            elif envio and abierto:
                rol = 'ENVIO_ABIERTO'
            else:
                rol = 'USO'
            self.enlazar(f, rol, grabado, fabricacion, envio)

    @staticmethod
    def ultimo_valor(filas, campo, largo, saltar_eventos=False):
        """Valor no vacío de la fila más reciente que lo tenga."""
        for f in sorted(filas, key=lambda f: (f['actualizado_el'], f['id']), reverse=True):
            valor = (f[campo] or '').strip()
            if valor and not (saltar_eventos and es_evento(valor)):
                return valor[:largo]
        return ''

    def datos_grabado(self, clave, filas):
        """Cliente / referencia / sobre de la API; lo que falte, de las filas legadas
        (la descripción se salta si es un evento "FALLO:" / "Físico:"), marcando
        datos_manuales. Misma regla que services._datos_grabado_nuevo."""
        info = self.externos.get(clave, {})
        api = datos_externos_utiles(info)
        legado = {
            'cliente': self.ultimo_valor(filas, 'cliente', 150),
            'referencia': self.ultimo_valor(filas, 'descripcion', 255, saltar_eventos=True),
            'sobre': self.ultimo_valor(filas, 'sobre', 100),
        }
        datos = {}
        manuales = not info.get('encontrado_ext')
        for campo in ('cliente', 'referencia', 'sobre'):
            if api[campo]:
                datos[campo] = api[campo]
            else:
                datos[campo] = legado[campo]
                manuales = manuales or bool(legado[campo]) or campo != 'sobre'
        datos['cliente'] = datos['cliente'][:150]
        datos['referencia'] = datos['referencia'][:255]
        datos['sobre'] = datos['sobre'][:100] or None
        datos['datos_manuales'] = manuales
        return datos

    def crear_fabricacion(self, grabado, f, numero, tipo):
        fabricacion = FabricacionGrabado.objects.create(
            grabado=grabado, numero=numero, tipo=tipo,
            responsables=f['responsables'], peso_inicial=f['peso_inicial'],
            peso_final=f['peso_final'], perdida=f['perdida'], temp=f['temp'], rpm=f['rpm'],
            # tiempo y compensación se copian tal cual como texto (regla 11).
            tiempo=f['tiempo'], compensacion=f['compensacion'],
            compensacion_motivo=f['compensacion_motivo'],
            bano_ml=calcular_bano_ml(f['perdida']),
            revisar_pesos=(fecha_local(f['creado_el']) < FECHA_CORTE_PESOS
                           and any(f[c] is not None for c in CAMPOS_PESO)),
            registrado_por_id=f['usuario_id'], registrado_el=f['creado_el'],
        )
        self.fabricaciones[tipo] += 1
        self.fabricaciones['revisar_pesos'] += fabricacion.revisar_pesos
        return fabricacion

    def crear_envio(self, grabado, f, abierto):
        """COMPLETADO / REPETIR / REVISION -> envío cerrado; EN_MAQUINA -> envío
        abierto si es la fila que define el estado de un grabado nuevo. Un
        EN_MAQUINA que no puede quedar abierto se cierra y se reporta."""
        if f['estado'] not in ESTADO_FISICO_ENVIO and f['estado'] != 'EN_MAQUINA':
            return None
        if f['estado'] == 'EN_MAQUINA' and not abierto:
            self.conflictos.append(f'{self.desc(f)}: EN_MAQUINA que no puede quedar abierto '
                                   '(no es la fila más reciente o el grabado ya existía); se cierra '
                                   'sin estado físico.')
        self.envios['abiertos' if abierto else 'cerrados'] += 1
        descripcion = (f['descripcion'] or '').strip()
        return EnvioMaquina.objects.create(
            grabado=grabado, of=f['of_norm'], maquina=self.maquina(f['maquina']),
            fecha_programada=f['fecha_programada'], cantidad_formatos=f['cantidad_formatos'],
            horas_proceso=f['horas_proceso'], papel=(f['papel'] or '')[:100] or None,
            enviado_por_id=f['usuario_id'], enviado_el=f['creado_el'],
            recogido_por_id=None if abierto else f['usuario_id'],
            recogido_el=None if abierto else f['actualizado_el'],
            estado_fisico=ESTADO_FISICO_ENVIO.get(f['estado']),
            comentario=descripcion if es_evento(descripcion) else None,
            ubicacion=(f['ubicacion'] or '')[:200] or None,
        )

    def maquina(self, nombre):
        forma = normalizar_nombre_maquina(nombre) or MAQUINA_SIN_NOMBRE
        if forma not in self.maquinas:
            self.maquinas[forma] = Maquina.objects.create(nombre=forma,
                                                          activa=forma != MAQUINA_SIN_NOMBRE)
            self.maquinas_creadas.append(forma)
        return self.maquinas[forma]

    # ======================================================== secciones finales
    def seccion_repeticiones(self):
        self.r.titulo('6. FILAS DETECTADAS COMO REPETICION')
        self.r.conteo('Filas', len(self.repeticiones))
        self.r.lista(self.repeticiones)

    def seccion_conflictos(self):
        self.r.titulo('7. CONFLICTOS CON GRABADOS CREADOS DESDE ALTA (y envíos que no quedan abiertos)')
        self.r.conteo('Grabados que ya existían y se reutilizan', self.grabados_reutilizados)
        self.r.conteo('Conflictos', len(self.conflictos), alerta=True)
        self.r.lista(self.conflictos)

    def seccion_conteos_finales(self):
        self.r.titulo('8. CONTEOS FINALES' + ('' if self.aplicar else ' (simulados)'))
        self.r.conteo('Grabados nuevos', getattr(self, 'grabados_nuevos', 0))
        self.r.conteo('Grabados reutilizados', getattr(self, 'grabados_reutilizados', 0))
        maquinas_creadas = getattr(self, 'maquinas_creadas', [])
        self.r.conteo('Máquinas creadas en el catálogo', len(maquinas_creadas))
        self.r.lista(maquinas_creadas)

        self.r.linea('\n  Grabados que crea la migración, por estado:')
        for estado, n in sorted(getattr(self, 'estados_nuevos', Counter()).items()):
            self.r.conteo(f'  {estado}', n)
        self.r.linea('\n  Grabados que ya estaban en la base (Alta / pruebas), por estado:')
        for estado, n in sorted(getattr(self, 'estados_previos', Counter()).items()):
            self.r.conteo(f'  {estado}', n)

        fabricaciones = getattr(self, 'fabricaciones', Counter())
        self.r.linea('\n  Fabricaciones que crea la migración:')
        for tipo in ('INICIAL', 'REPETICION'):
            self.r.conteo(f'  {tipo}', fabricaciones[tipo])
        self.r.conteo('  marcadas para revisar pesos', fabricaciones['revisar_pesos'])

        envios = getattr(self, 'envios', Counter())
        self.r.linea('\n  Envíos a máquina que crea la migración:')
        self.r.conteo('  cerrados (COMPLETADO / REPETIR / REVISION)', envios['cerrados'])
        self.r.conteo('  abiertos (EN_MAQUINA)', envios['abiertos'])

        self.r.linea('\n  LegadoOrden de esta corrida, por rol (uno por fila; una fila con envío')
        self.r.linea('  queda como FABRICACION si además fue fabricación, si no como USO o ENVIO_ABIERTO):')
        for rol, n in sorted(getattr(self, 'roles', Counter()).items()):
            self.r.conteo(f'  {rol}', n)

        legados, ordenes = self.conciliacion
        self.r.linea('')
        texto = f'  Conciliación: LegadoOrden={legados}  OrdenFabricacion={ordenes}'
        if legados == ordenes:
            self.r.linea(texto + '  -> CUADRA', self.style.SUCCESS)
        else:
            self.r.linea(texto + '  -> NO CUADRA', self.style.ERROR)

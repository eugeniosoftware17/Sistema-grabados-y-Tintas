"""
Auditoría de SOLO LECTURA de los datos actuales de gestion_grabados, previa a la
reestructuración Grabado / Fabricacion / EnvioMaquina.

No escribe nada: solo hace SELECT, y además todo corre dentro de una transacción
que se revierte siempre al terminar (aunque algo intentara escribir).

Uso:
    python manage.py auditar_grabados
    python manage.py auditar_grabados --limite 0        # listar todos los casos
    python manage.py auditar_grabados --externa         # además consulta FileMaker / externa_2012
"""
import os
import re
from collections import Counter, defaultdict

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.gestion_grabados.models import EstadoBano, OrdenFabricacion

PROCESOS_VALIDOS = ('STAMPING', 'EMBOSSING')
REFERENCIAS_VACIAS = ('', '—', '-', 'NAN', 'NONE', '0')
CAMPOS_TECNICOS = ('peso_inicial', 'peso_final', 'temp', 'rpm', 'tiempo', 'compensacion')
GRUPO_SUPERVISORES = 'Supervisores de Producción'


def normalizar_of(valor):
    """Misma regla que sincronizar_plani / _normalizar_of: "22750.0" -> "22750",
    "22651-A" -> "22651". Devuelve '' si no queda ningún dígito."""
    if valor is None:
        return ''
    texto = str(valor).strip()
    if '.' in texto:
        texto = texto.split('.')[0]
    return re.sub(r'\D', '', texto)


def referencia_valida(fila):
    """OF de referencia normalizada, o '' si no hay o apunta a la propia OF."""
    ref = (fila['referencia'] or '').strip()
    if ref.upper() in REFERENCIAS_VACIAS:
        return ''
    ref_norm = normalizar_of(ref)
    return '' if ref_norm == fila['of_norm'] else ref_norm


def es_numero(valor):
    if valor is None or str(valor).strip() == '':
        return True
    try:
        float(str(valor).strip().replace(',', '.'))
        return True
    except ValueError:
        return False


class Command(BaseCommand):
    help = 'Auditoría de solo lectura de OrdenFabricacion antes de la reestructuración.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=30,
                            help='Máximo de casos a listar por sección (0 = todos).')
        parser.add_argument('--externa', action='store_true',
                            help='Consultar también FileMaker / externa_2012 por cada OF de origen.')

    def handle(self, *args, **opts):
        self.limite = opts['limite']
        with transaction.atomic():
            try:
                self.auditar(opts['externa'])
            finally:
                transaction.set_rollback(True)

    # ------------------------------------------------------------------ salida
    def titulo(self, texto):
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('=' * 78))
        self.stdout.write(self.style.MIGRATE_HEADING(texto))
        self.stdout.write(self.style.MIGRATE_HEADING('=' * 78))

    def linea(self, texto=''):
        self.stdout.write(texto)

    def conteo(self, etiqueta, valor, alerta=False):
        txt = f'  {etiqueta:<58} {valor:>8}'
        self.stdout.write(self.style.WARNING(txt) if alerta and valor else txt)

    def listar(self, items, formato):
        mostrar = items if self.limite == 0 else items[:self.limite]
        for item in mostrar:
            self.linea('    ' + formato(item))
        if len(mostrar) < len(items):
            self.linea(f'    ... y {len(items) - len(mostrar)} más (usar --limite 0 para ver todos)')

    @staticmethod
    def desc(f):
        return f"id={f['id']} OF={f['of']!r} {f['proceso']} {f['estado']}" + \
               (' [manual]' if f['origen_manual'] else '')

    # --------------------------------------------------------------- auditoría
    def auditar(self, con_externa):
        db = settings.DATABASES['default']
        self.titulo('AUDITORÍA gestion_grabados (solo lectura)')
        self.linea(f"  Base: {db.get('NAME')} @ {db.get('HOST')}")

        filas = list(OrdenFabricacion.objects.order_by('id').values(
            'id', 'of', 'referencia', 'proceso', 'estado', 'origen_manual', 'descripcion',
            'responsables', 'ubicacion', 'sobre', 'maquina', 'usos_acumulados', 'foto_dano',
            'compensacion_motivo', 'actualizado_el', *CAMPOS_TECNICOS,
        ))
        for f in filas:
            f['of_norm'] = normalizar_of(f['of'])
            f['ref_norm'] = referencia_valida(f)
            f['clave'] = (f['ref_norm'] or f['of_norm'], f['proceso'])
            f['tecnicos'] = any(f[c] not in (None, '') for c in CAMPOS_TECNICOS)

        self.seccion_conteos(filas)
        self.seccion_calidad(filas)
        colisiones = self.seccion_colisiones(filas)
        self.seccion_referencias(filas)
        self.seccion_grupos(filas)
        self.seccion_tecnicos(filas)
        self.seccion_fotos(filas)
        self.seccion_bano_y_permisos()
        if con_externa:
            self.seccion_externa(filas)

        self.titulo('RESUMEN')
        if colisiones:
            self.stdout.write(self.style.ERROR(
                f'  {colisiones} grupo(s) de OF que chocan al normalizar: hay que resolverlos '
                'antes de la migración de datos.'))
        else:
            self.stdout.write(self.style.SUCCESS('  Sin colisiones de OF normalizada.'))
        self.linea('  No se escribió nada en la base (transacción revertida).')

    def seccion_conteos(self, filas):
        self.titulo('1. CONTEOS')
        self.conteo('Total de filas en OrdenFabricacion', len(filas))

        self.linea('\n  Por estado (todas):')
        for estado, n in sorted(Counter(f['estado'] for f in filas).items()):
            self.conteo(f'  {estado}', n)

        self.linea('\n  Por origen y estado:')
        por_origen = Counter(('Fabricación (manual)' if f['origen_manual'] else 'PLANI', f['estado'])
                             for f in filas)
        for (origen, estado), n in sorted(por_origen.items()):
            self.conteo(f'  {origen:<22} {estado}', n)

        self.linea('\n  Por proceso:')
        for proceso, n in sorted(Counter(f['proceso'] for f in filas).items()):
            self.conteo(f'  {proceso}', n)

        self.linea('\n  Mapeo acordado que afecta a estas filas:')
        self.conteo('  REVISION -> REPETIR', sum(f['estado'] == 'REVISION' for f in filas))
        self.conteo('  Manual PENDIENTE -> APROBADO (LEGADO)',
                    sum(f['origen_manual'] and f['estado'] == 'PENDIENTE' for f in filas))
        self.conteo('  EN_MAQUINA -> envío abierto', sum(f['estado'] == 'EN_MAQUINA' for f in filas))

    def seccion_calidad(self, filas):
        self.titulo('2. VALORES FUERA DE LO ESPERADO')
        estados_validos = dict(OrdenFabricacion.ESTADO_CHOICES)

        casos = [
            ('Proceso distinto de STAMPING/EMBOSSING',
             [f for f in filas if f['proceso'] not in PROCESOS_VALIDOS]),
            ('Estado fuera de ESTADO_CHOICES',
             [f for f in filas if f['estado'] not in estados_validos]),
            ('OF sin ningún dígito (no se puede normalizar)',
             [f for f in filas if not f['of_norm']]),
            ('OF que cambia al normalizar (ej. "22651-A" -> "22651")',
             [f for f in filas if f['of_norm'] and f['of_norm'] != str(f['of']).strip()]),
            ('Descripción pisada por un evento ("FALLO:" / "Físico:")',
             [f for f in filas if (f['descripcion'] or '').startswith(('FALLO:', 'Físico:'))]),
            ('Estado CANCELADO', [f for f in filas if f['estado'] == 'CANCELADO']),
        ]
        for etiqueta, items in casos:
            alerta = 'Descripción' not in etiqueta
            self.conteo(etiqueta, len(items), alerta=alerta)
            if alerta and items:
                self.listar(items, lambda f: self.desc(f) + f" -> normalizada {f['of_norm']!r}")

    def seccion_colisiones(self, filas):
        self.titulo('3. OF QUE CHOCAN AL NORMALIZAR (misma OF normalizada + proceso)')
        grupos = defaultdict(list)
        for f in filas:
            if f['of_norm']:
                grupos[(f['of_norm'], f['proceso'])].append(f)
        chocan = [(k, v) for k, v in sorted(grupos.items()) if len(v) > 1]
        self.conteo('Grupos con más de una fila', len(chocan), alerta=True)
        self.listar(chocan, lambda kv: f"OF {kv[0][0]} {kv[0][1]}: " +
                    ' | '.join(self.desc(f) for f in kv[1]))
        return len(chocan)

    def seccion_referencias(self, filas):
        self.titulo('4. OF REFERENCIA (de dónde sale la OF de origen del grabado)')
        por_clave = {(f['of_norm'], f['proceso']): f for f in filas}
        ofs_por_norm = defaultdict(set)
        for f in filas:
            ofs_por_norm[f['of_norm']].add(f['proceso'])

        con_ref = [f for f in filas if f['ref_norm']]
        self.conteo('Filas con OF referencia distinta de la propia OF', len(con_ref))
        self.conteo('Filas sin referencia (la propia OF es la de origen)', len(filas) - len(con_ref))

        manuales_con_ref = [f for f in con_ref if f['origen_manual']]
        self.conteo('Altas manuales (stock) con referencia distinta de su OF', len(manuales_con_ref), alerta=True)
        self.linea('    -> para estas hay que decidir si la OF de origen es la OF cargada o su referencia')
        self.listar(manuales_con_ref, lambda f: self.desc(f) + f" ref={f['referencia']!r}")

        huerfanas = [f for f in con_ref if (f['ref_norm'], f['proceso']) not in por_clave]
        self.conteo('Referencia a una OF que no existe como fila (mismo proceso)', len(huerfanas))
        solo_otro = [f for f in huerfanas if ofs_por_norm.get(f['ref_norm'])]
        self.conteo('  ... de ellas, la OF existe pero solo en el OTRO proceso', len(solo_otro), alerta=True)
        self.listar(solo_otro, lambda f: self.desc(f) + f" ref={f['ref_norm']} existe en "
                    f"{sorted(ofs_por_norm[f['ref_norm']])}")

        cadenas = [f for f in con_ref
                   if (f['ref_norm'], f['proceso']) in por_clave
                   and por_clave[(f['ref_norm'], f['proceso'])]['ref_norm']]
        self.conteo('Referencia en cadena (la OF referida tiene a su vez otra referencia)',
                    len(cadenas), alerta=True)
        self.listar(cadenas, lambda f: self.desc(f) + f" -> {f['ref_norm']} -> "
                    f"{por_clave[(f['ref_norm'], f['proceso'])]['ref_norm']}")

    def seccion_grupos(self, filas):
        self.titulo('5. GRABADOS RESULTANTES (agrupando por OF de origen + proceso)')
        grupos = defaultdict(list)
        for f in filas:
            if f['clave'][0]:
                grupos[f['clave']].append(f)

        self.conteo('Grabados distintos (of_origen, proceso)', len(grupos))
        tamanos = Counter(len(v) for v in grupos.values())
        for tam, n in sorted(tamanos.items()):
            self.conteo(f'  con {tam} fila(s)', n)

        varias_manuales = [(k, v) for k, v in grupos.items() if sum(f['origen_manual'] for f in v) > 1]
        self.conteo('Grabados con más de un alta manual', len(varias_manuales), alerta=True)
        self.listar(varias_manuales, lambda kv: f"{kv[0]}: " + ' | '.join(self.desc(f) for f in kv[1]))

        mixtos = [(k, v) for k, v in grupos.items()
                  if any(f['origen_manual'] for f in v) and not all(f['origen_manual'] for f in v)]
        self.conteo('Grabados con alta manual + filas del PLANI (se unen)', len(mixtos))

        varios_en_maquina = [(k, v) for k, v in grupos.items()
                             if sum(f['estado'] == 'EN_MAQUINA' for f in v) > 1]
        self.conteo('Grabados con más de una fila EN_MAQUINA a la vez', len(varios_en_maquina), alerta=True)
        self.listar(varios_en_maquina, lambda kv: f"{kv[0]}: " +
                    ' | '.join(self.desc(f) for f in kv[1] if f['estado'] == 'EN_MAQUINA'))

        self.linea('\n  Estado de la fila más reciente de cada grabado (sería la fabricación vigente):')
        vigentes = Counter(max(v, key=lambda f: f['actualizado_el'])['estado'] for v in grupos.values())
        for estado, n in sorted(vigentes.items()):
            self.conteo(f'  {estado}', n)

    def seccion_tecnicos(self, filas):
        self.titulo('6. DATOS TÉCNICOS')
        con_tec = [f for f in filas if f['tecnicos']]
        self.conteo('Filas con algún dato técnico', len(con_tec))
        for estado, n in sorted(Counter(f['estado'] for f in con_tec).items()):
            self.conteo(f'  {estado}', n)
        sin_tec = Counter(f['estado'] for f in filas if not f['tecnicos'])
        self.linea('\n  Filas SIN datos técnicos, por estado:')
        for estado, n in sorted(sin_tec.items()):
            self.conteo(f'  {estado}', n)

        for campo in ('tiempo', 'compensacion'):
            malos = [f for f in filas if not es_numero(f[campo])]
            self.conteo(f'"{campo}" no convertible a número', len(malos), alerta=True)
            self.listar(malos, lambda f, c=campo: self.desc(f) + f' {c}={f[c]!r}')

        self.conteo('Filas con usos_acumulados > 0', sum((f['usos_acumulados'] or 0) > 0 for f in filas))
        self.conteo('Suma de usos_acumulados', sum(f['usos_acumulados'] or 0 for f in filas))
        self.conteo('Filas con motivo de compensación', sum(bool(f['compensacion_motivo']) for f in filas))

    def seccion_fotos(self, filas):
        self.titulo('7. FOTOS DE DAÑO')
        con_foto = [f for f in filas if f['foto_dano']]
        self.conteo('Filas con foto_dano', len(con_foto))
        if not con_foto:
            return
        media_root = getattr(settings, 'MEDIA_ROOT', '') or ''
        bases = [('MEDIA_ROOT', media_root)] if media_root else []
        bases += [('BASE_DIR', str(settings.BASE_DIR)), ('cwd', os.getcwd())]
        self.linea(f"  MEDIA_ROOT actual: {media_root!r}")
        for nombre, base in bases:
            existen = sum(os.path.exists(os.path.join(base, f['foto_dano'])) for f in con_foto)
            self.conteo(f'  archivos encontrados bajo {nombre}', existen)
        faltan = [f for f in con_foto
                  if not any(os.path.exists(os.path.join(b, f['foto_dano'])) for _, b in bases)]
        self.conteo('Fotos cuyo archivo no aparece en ninguna de esas rutas', len(faltan), alerta=True)
        self.listar(faltan, lambda f: self.desc(f) + f" {f['foto_dano']}")

    def seccion_bano_y_permisos(self):
        self.titulo('8. BAÑO, USUARIOS Y PERMISOS')
        banos = list(EstadoBano.objects.values('id', 'ml_acumulados', 'ultima_renovacion', 'renovado_por'))
        self.conteo('Filas en EstadoBano', len(banos))
        for b in banos:
            self.linea(f"    id={b['id']} ml_acumulados={b['ml_acumulados']:.1f} / {EstadoBano.LIMITE_ML} "
                       f"última renovación={b['ultima_renovacion']} por={b['renovado_por']}")
        self.conteo('Usuarios activos', User.objects.filter(is_active=True).count())
        self.conteo('  de ellos staff', User.objects.filter(is_active=True, is_staff=True).count())
        existe = Group.objects.filter(name=GRUPO_SUPERVISORES).exists()
        self.linea(f'  Grupo "{GRUPO_SUPERVISORES}": {"ya existe" if existe else "no existe (se crea en la migración)"}')

    def seccion_externa(self, filas):
        self.titulo('9. OF DE ORIGEN EN FILEMAKER / externa_2012')
        # Import local: views.py importa pandas y la configuración externa, solo hace falta acá.
        from apps.gestion_grabados.views import buscar_datos_externos_batch
        self.linea(f"  Origen configurado: {getattr(settings, 'EXTERNA_2012_SOURCE', 'db')}")
        for proceso in PROCESOS_VALIDOS:
            origenes = sorted({f['clave'][0] for f in filas if f['proceso'] == proceso and f['clave'][0]})
            if not origenes:
                continue
            info = buscar_datos_externos_batch(origenes, proceso)
            faltan = [of for of in origenes if not info.get(int(of), {}).get('encontrado_ext')]
            self.conteo(f'{proceso}: OF de origen consultadas', len(origenes))
            self.conteo(f'{proceso}: no encontradas (quedarían como datos_manuales)', len(faltan), alerta=True)
            self.listar(faltan, lambda of: f'OF {of}')

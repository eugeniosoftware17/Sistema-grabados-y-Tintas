import pandas as pd
import json
import os
import re
import io
import requests
import urllib3
from django.shortcuts import render
from django.http import JsonResponse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from .models import OrdenFabricacion, EstadoBano
from django.db import connections

# La FileMaker Data API (CRDAPS10) usa un certificado autofirmado; se acepta el
# mismo riesgo de MITM en LAN que ya asume la conexión a SQL Server (TrustServerCertificate=yes).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Nombre de campo en la tabla local (CigarRings2012) -> nombre de campo tal como lo
# expone la FileMaker Data API (tabla "cigar rings FP10", con espacios en vez de "_").
FIELD_MAP_FILEMAKER = {
    'G_orden': 'G_orden',
    'OF_Stamping': 'OF Stamping',
    'OF_Embossing': 'OF Embossing',
    'Acabat_Stamping': 'Acabat Stamping',
    'Acabat_Embossing': 'Acabat Embossing',
    'Sobre_pelicula': 'Sobre pelicula',
    'G_Cliente': 'G_Cliente',
    'G_Referencia': 'G_Referencia',
}


# Columnas de "info" que se piden siempre, sin importar el proceso (para poder
# resolver STAMPING o EMBOSSING con la misma fila y para inferir proceso_ext).
# El orden acá define el orden posicional que devuelven las funciones _buscar_*.
COLUMNAS_INFO_EXTERNA = [
    'Sobre_pelicula', 'OF_Stamping', 'OF_Embossing',
    'Acabat_Stamping', 'Acabat_Embossing', 'G_Cliente', 'G_Referencia',
]

VACIO_INFO_EXTERNA = {
    'encontrado_ext': False, 'sobre_ext': '—', 'ref_ext': '—', 'acabado_ext': '0',
    'proceso_ext': None, 'cliente_ext': '—', 'descripcion_ext': '—',
    'of_stamping_ext': '—', 'of_embossing_ext': '—',
}


def _normalizar_of(of_numero):
    """"22651.0" -> 22651; descarta todo lo que no sea dígito. None si no queda nada numérico."""
    of_str = str(of_numero).strip()
    if '.' in of_str:
        of_str = of_str.split('.')[0]
    of_limpia = re.sub(r'\D', '', of_str)
    return int(of_limpia) if of_limpia else None


def _procesar_fila_externa(row, proceso):
    """Convierte una fila en el orden de COLUMNAS_INFO_EXTERNA al dict que consume
    el resto de la app (mismas claves que antes devolvía buscar_datos_externos)."""
    sobre, of_stamping, of_embossing, acabat_stamping, acabat_embossing, cliente, referencia = row

    col_acabado = acabat_stamping if proceso == 'STAMPING' else acabat_embossing
    val_acabado = str(col_acabado).strip().lower() if col_acabado is not None else '0'
    if val_acabado in ['si', 'sí', 'true', '1', '1.0', 'ok', 's', 'y']:
        val_acabado = '1'
    else:
        val_acabado = '0'

    def tiene_valor(v):
        return v is not None and str(v).strip() not in ('', '—')

    tiene_stamping = tiene_valor(of_stamping)
    tiene_embossing = tiene_valor(of_embossing)
    if tiene_stamping and not tiene_embossing:
        proceso_ext = 'STAMPING'
    elif tiene_embossing and not tiene_stamping:
        proceso_ext = 'EMBOSSING'
    else:
        # Ambos u ninguno poblados: no se puede inferir un único proceso.
        proceso_ext = None

    ref = of_stamping if proceso == 'STAMPING' else of_embossing

    return {
        'sobre_ext': sobre if sobre else '—',
        'ref_ext': ref if ref else '—',
        'acabado_ext': val_acabado,
        'proceso_ext': proceso_ext,
        'cliente_ext': cliente if cliente else '—',
        'descripcion_ext': referencia if referencia else '—',
        'of_stamping_ext': of_stamping if of_stamping else '—',
        'of_embossing_ext': of_embossing if of_embossing else '—',
        'encontrado_ext': True,
    }


def _buscar_fila_db(of_int, columnas):
    """Consulta CigarRings2012 en externa_2012 por SQL directo. Devuelve la fila o None."""
    with connections['externa_2012'].cursor() as cursor:
        query = f"SELECT {', '.join(columnas)} FROM CigarRings2012 WHERE G_orden = %s"
        cursor.execute(query, [of_int])
        return cursor.fetchone()


def _buscar_fila_api(of_int, columnas):
    """Consulta la FileMaker Data API (o su mock local, ver api_simulada.py) por HTTP.
    Devuelve la fila en el mismo orden que `columnas` o None."""
    columnas_fm = [FIELD_MAP_FILEMAKER.get(c, c) for c in columnas]
    select_clause = ', '.join(f'"{c}"' for c in columnas_fm)
    query = f'SELECT {select_clause} FROM "cigar rings FP10" WHERE "G_orden" = {of_int}'
    payload = {
        'connection': {
            'host': settings.FM_HOST_NAME,
            'dsn': settings.FM_DSN,
            'uid': settings.FM_USER,
            'pwd': settings.FM_PASSWORD,
        },
        'query': query,
    }
    resp = requests.post(settings.FM_URL, json=payload, verify=False, timeout=10)
    resp.raise_for_status()
    filas = resp.json()
    if not filas:
        return None
    fila = filas[0]
    return [fila.get(campo_fm) for campo_fm in columnas_fm]


def _buscar_filas_db_batch(of_ints):
    """Igual que _buscar_fila_db pero para muchas G_orden en una sola consulta.
    Devuelve {G_orden: fila} con fila en el orden de COLUMNAS_INFO_EXTERNA."""
    if not of_ints:
        return {}
    with connections['externa_2012'].cursor() as cursor:
        placeholders = ', '.join(['%s'] * len(of_ints))
        columnas = ', '.join(['G_orden'] + COLUMNAS_INFO_EXTERNA)
        query = f"SELECT {columnas} FROM CigarRings2012 WHERE G_orden IN ({placeholders})"
        cursor.execute(query, of_ints)
        return {row[0]: row[1:] for row in cursor.fetchall()}


def _buscar_filas_api_batch(of_ints):
    """Igual que _buscar_fila_api pero para muchas G_orden en una sola consulta HTTP
    (un solo POST con WHERE "G_orden" IN (...) en vez de uno por OF).
    Devuelve {G_orden: fila} con fila en el orden de COLUMNAS_INFO_EXTERNA."""
    if not of_ints:
        return {}
    columnas = ['G_orden'] + COLUMNAS_INFO_EXTERNA
    columnas_fm = [FIELD_MAP_FILEMAKER.get(c, c) for c in columnas]
    select_clause = ', '.join(f'"{c}"' for c in columnas_fm)
    lista_ids = ', '.join(str(i) for i in of_ints)  # of_ints son siempre int (ver _normalizar_of)
    query = f'SELECT {select_clause} FROM "cigar rings FP10" WHERE "G_orden" IN ({lista_ids})'
    payload = {
        'connection': {
            'host': settings.FM_HOST_NAME,
            'dsn': settings.FM_DSN,
            'uid': settings.FM_USER,
            'pwd': settings.FM_PASSWORD,
        },
        'query': query,
    }
    resp = requests.post(settings.FM_URL, json=payload, verify=False, timeout=30)
    resp.raise_for_status()
    resultado = {}
    for fila in resp.json():
        g_orden = fila.get(columnas_fm[0])
        if g_orden is None:
            continue
        resultado[int(g_orden)] = [fila.get(campo_fm) for campo_fm in columnas_fm[1:]]
    return resultado


def buscar_datos_externos(of_numero, proceso):
    """Busca información técnica de STAMPING/EMBOSSING para una única OF usando G_orden.
    Origen configurable por settings.EXTERNA_2012_SOURCE: 'db' (SQL directo a
    externa_2012) o 'api' (FileMaker Data API / mock local).
    Para muchas OF a la vez (ej. sincronizar_plani) usar buscar_datos_externos_batch,
    que hace una sola consulta en vez de una por OF."""
    of_int = _normalizar_of(of_numero)
    if of_int is None:
        return dict(VACIO_INFO_EXTERNA)
    try:
        if getattr(settings, 'EXTERNA_2012_SOURCE', 'db') == 'api':
            row = _buscar_fila_api(of_int, COLUMNAS_INFO_EXTERNA)
        else:
            row = _buscar_fila_db(of_int, COLUMNAS_INFO_EXTERNA)
        if row:
            return _procesar_fila_externa(row, proceso)
    except Exception as e:
        print(f"Error consultando datos externos para OF {of_numero}: {e}")
    return dict(VACIO_INFO_EXTERNA)


def buscar_datos_externos_batch(of_numeros, proceso):
    """Versión en lote de buscar_datos_externos(): una sola consulta (SQL o HTTP) para
    todas las OF de `of_numeros`, en vez de una por OF. Pensada para sincronizar_plani(),
    donde todas las filas de una misma hoja comparten el mismo `proceso`.
    Devuelve {G_orden: info_dict}, con las mismas claves que buscar_datos_externos()."""
    of_ints = sorted({of_int for of_int in (_normalizar_of(n) for n in of_numeros) if of_int is not None})

    filas = {}
    try:
        if getattr(settings, 'EXTERNA_2012_SOURCE', 'db') == 'api':
            filas = _buscar_filas_api_batch(of_ints)
        else:
            filas = _buscar_filas_db_batch(of_ints)
    except Exception as e:
        print(f"Error consultando datos externos en lote ({proceso}, {len(of_ints)} OF): {e}")

    return {
        of_int: (_procesar_fila_externa(filas[of_int], proceso) if of_int in filas else dict(VACIO_INFO_EXTERNA))
        for of_int in of_ints
    }

@login_required
def grabado_consulta(request):
    return render(request, 'grabados_tabla.html')

@login_required
def plani_consulta(request):
    return render(request, 'plani_tabla.html')

@login_required
def api_obtener_registros(request):
    registros = list(OrdenFabricacion.objects.all().values(
        'of', 'referencia', 'descripcion', 'cliente', 
        'tipo_grabado', 'proceso', 'maquina', 'estado', 
        'fecha_programada', 'fecha_registro', 'ubicacion', 'sobre',
        'cantidad_formatos', 'horas_proceso',
        'responsables', 'peso_inicial', 'peso_final', 
        'perdida', 'temp', 'rpm', 'tiempo', 'compensacion',
        'usos_acumulados', 'foto_dano'
    ))

    for r in registros:
        r['ref']      = r.pop('referencia')
        r['desc']     = r.pop('descripcion')
        r['tipo']     = r.pop('tipo_grabado')
        
        f_prog = r.pop('fecha_programada')
        r['fecha'] = f_prog.strftime('%d/%m/%Y') if f_prog else '—'
        
        f_reg = r.pop('fecha_registro')
        r['fecha_reg'] = f_reg.strftime('%d/%m/%Y') if f_reg else '—'
        r['cantidad'] = r.pop('cantidad_formatos')
        r['horas']    = r.pop('horas_proceso')
        r['peso_i']   = r.pop('peso_inicial')
        r['peso_f']   = r.pop('peso_final')
        r['tiempo_t'] = r.pop('tiempo')
        r['comp']     = r.pop('compensacion')

    return JsonResponse(registros, safe=False)

@login_required
def grabado_estadisticas(request):
    return render(request, 'grabados_estadisticas.html')

# API para estadísticas detalladas
@login_required
def api_estadisticas_detalladas(request):
    from django.db.models import Count, Sum, Avg
    from django.db.models.functions import TruncMonth
    
    # 1. Distribución por Proceso
    por_proceso = list(OrdenFabricacion.objects.values('proceso').annotate(total=Count('id')))
    
    # 2. Distribución por Máquina (solo completados)
    por_maquina = list(OrdenFabricacion.objects.filter(estado='COMPLETADO').values('maquina').annotate(total=Count('id')))
    
    # 3. Rendimiento por Operario (Top 10)
    por_operario = list(OrdenFabricacion.objects.filter(estado='COMPLETADO')
                        .values('responsables')
                        .annotate(total=Count('id'))
                        .order_by('-total')[:10])
    
    # 4. Calidad (OK vs Repetir)
    calidad = list(OrdenFabricacion.objects.values('estado')
                   .filter(estado__in=['COMPLETADO', 'REPETIR'])
                   .annotate(total=Count('id')))

    # 5. Métricas de Medición Técnica
    metricas_tecnicas = OrdenFabricacion.objects.filter(estado='COMPLETADO').aggregate(
        perdida_avg=Avg('perdida'),
        rpm_avg=Avg('rpm'),
        temp_avg=Avg('temp'),
        peso_total=Sum('peso_inicial')
    )

    return JsonResponse({
        'proceso': por_proceso,
        'maquina': por_maquina,
        'operario': por_operario,
        'calidad': calidad,
        'tecnico': {
            'perdida_avg': round(metricas_tecnicas['perdida_avg'] or 0, 2),
            'rpm_avg': round(metricas_tecnicas['rpm_avg'] or 0, 0),
            'temp_avg': round(metricas_tecnicas['temp_avg'] or 0, 1),
            'peso_total': round(metricas_tecnicas['peso_total'] or 0, 1)
        }
    })

# API para obtener KPIs del Dashboard
@login_required
def api_dashboard_kpis(request):
    from django.utils import timezone
    from django.db.models import Q
    hoy = timezone.now().date()
    
    kpis = {
        'en_maquina': OrdenFabricacion.objects.filter(estado='EN_MAQUINA').count(),
        'completados_hoy': OrdenFabricacion.objects.filter(
            Q(estado='COMPLETADO') | Q(estado='REPETIR'),
            actualizado_el__date=hoy
        ).count(),
        'para_repetir': OrdenFabricacion.objects.filter(estado='REPETIR').count(),
        'pendientes': OrdenFabricacion.objects.filter(estado='PENDIENTE').count(),
    }
    return JsonResponse(kpis)

@login_required
def api_historial_orden(request, of_numero):
    # 1. Obtener historial de la OF principal
    campos_tecnicos = [
        'of', 'referencia', 'proceso', 'estado', 'responsables', 'ubicacion', 
        'actualizado_el', 'descripcion', 'usuario__username',
        'peso_inicial', 'peso_final', 'temp', 'rpm', 'tiempo', 'usos_acumulados'
    ]
    
    registros_principales = list(OrdenFabricacion.objects.filter(of=of_numero).order_by('-actualizado_el').values(*campos_tecnicos))

    # 2. Detectar si hay una referencia vinculada
    ref_numero = None
    for r in registros_principales:
        if r['referencia'] and r['referencia'] != '—' and r['referencia'] != of_numero:
            ref_numero = r['referencia']
            break
    
    registros_referencia = []
    if ref_numero:
        registros_referencia = list(OrdenFabricacion.objects.filter(of=ref_numero).order_by('-actualizado_el').values(
            'of', 'referencia', 'proceso', 'estado', 'responsables', 'ubicacion', 'actualizado_el', 'descripcion',
            'usuario__username'
        ))

    return JsonResponse({
        'principal': registros_principales,
        'referencia': registros_referencia,
        'ref_id': ref_numero
    }, safe=False)

@login_required
def api_eliminar_registro(request):
    if request.method == 'POST':
        if not request.user.is_staff:
            return JsonResponse({'status': 'error', 'message': 'No tiene permisos para eliminar.'}, status=403)
        try:
            data = json.loads(request.body)
            of_num = data.get('of')
            proceso = data.get('proceso')
            OrdenFabricacion.objects.filter(of=of_num, proceso=proceso).delete()
            return JsonResponse({'status': 'ok', 'message': 'Registro eliminado.'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

@login_required
def sincronizar_plani(request):
    excel_path = getattr(settings, 'PLANI_EXCEL_PATH', None)
    if not excel_path or not os.path.exists(excel_path):
        return JsonResponse({'status': 'error', 'message': 'Archivo no encontrado'}, status=404)

    try:
        hojas_a_procesar = ['STAMPING', 'EMBOSSING']
        datos_totales = []
        stats = {
            'total_filas_excel': 0,
            'procesados_ok': 0,
            'con_error': 0,
            'duplicados_omitidos': 0,
            'errores_detalle': []
        }

        # Se lee el archivo a memoria en modo solo-lectura y se cierra de inmediato,
        # para no mantener el archivo abierto mientras otra persona lo edita en Excel.
        with open(excel_path, 'rb') as f:
            excel_bytes = io.BytesIO(f.read())

        xls = pd.ExcelFile(excel_bytes, engine='openpyxl')
        hojas_reales = [h for h in hojas_a_procesar if h in xls.sheet_names]

        for nombre_hoja in hojas_reales:
            df_full = pd.read_excel(xls, sheet_name=nombre_hoja, header=None)
            header_row = 0
            for index, row in df_full.iterrows():
                if any(str(val).strip().upper() == "ORDEN" for val in row):
                    header_row = index
                    break

            df = pd.read_excel(xls, sheet_name=nombre_hoja, header=header_row)
            df.columns = [str(c).strip().upper() for c in df.columns]

            mapeo = {
                'of': ['ORDEN', 'OF', 'ORDEN DE FABRICACIÓN'],
                'descripcion': ['REFERENCIA'],
                'cliente': ['CLIENTE', 'NOMBRE'],
                'horas_proceso': ['HORAS PROCESO', 'HORAS', 'PREV HR'],
                'fecha_programada': ['FECHA STAMPING', 'FECHA EMBOSSING', 'FECHA', 'DATE', 'FECHA PROG.'],
                # En el Excel, la columna "RESPONSABLE" contiene el nombre de la máquina
                # (ej: GIETZ 01, GIETZ 02, STAR FOIL, GTP), no una persona.
                'maquina': ['RESPONSABLE', 'MAQUINA', 'MÁQUINA']
            }

            columnas_finales = {}
            for campo, opciones in mapeo.items():
                for opcion in opciones:
                    if opcion in df.columns:
                        columnas_finales[campo] = opcion
                        break
            
            if 'of' not in columnas_finales: continue

            registros_locales = {
                str(r['of']).strip().upper(): r 
                for r in OrdenFabricacion.objects.filter(proceso=nombre_hoja).values(
                    'of', 'responsables', 'estado', 'ubicacion', 'sobre', 'proceso'
                )
            }

            ofs_procesadas_en_hoja = set()
            filas_validas = []  # (index, of_str, row) de filas únicas y con OF válida

            for index, row in df.iterrows():
                of_val = row[columnas_finales['of']]

                # NORMALIZACIÓN EXTREMA DE OF
                of_raw = str(of_val).strip()
                if not of_raw or of_raw.lower() == 'nan': continue

                # Manejar "22750.0" -> "22750"
                if '.' in of_raw:
                    of_raw = of_raw.split('.')[0]

                # Solo dígitos: "22651-A" -> "22651"
                of_str = re.sub(r'\D', '', of_raw)

                if not of_str: continue

                # Si llegamos aquí, es una fila que intentaremos procesar
                stats['total_filas_excel'] += 1

                # Detector de Duplicados
                if of_str in ofs_procesadas_en_hoja:
                    stats['duplicados_omitidos'] += 1
                    continue
                ofs_procesadas_en_hoja.add(of_str)
                filas_validas.append((index, of_str, row))

            # Una sola consulta (SQL o HTTP) para todas las OF de la hoja, en vez de
            # una por fila (antes: ~1 llamada por OF; ahora: 1 por hoja).
            datos_externos_hoja = buscar_datos_externos_batch(
                [of_str for _, of_str, _ in filas_validas], nombre_hoja
            )

            for index, of_str, row in filas_validas:
                try:
                    item = {'of': of_str, 'proceso': nombre_hoja, 'referencia': '—'}
                    for campo, col_excel in columnas_finales.items():
                        if campo == 'of': continue
                        val = row.get(col_excel)
                        if pd.isna(val): val = None
                        elif campo == 'fecha_programada':
                            if isinstance(val, pd.Timestamp): val = val.strftime('%d/%m/%Y')
                            else: val = str(val)
                        item[campo] = val

                    # Enriquecimiento con DB local y externa
                    registro_eis = registros_locales.get(of_str)
                    info_ext = datos_externos_hoja.get(int(of_str), dict(VACIO_INFO_EXTERNA))
                    item.update(info_ext)

                    es_listo_ext = (info_ext.get('acabado_ext') == '1')

                    if registro_eis:
                        item['status_db'] = 'existente'
                        item['responsable_db'] = registro_eis['responsables']
                        item['ubicacion_db'] = registro_eis['ubicacion']
                        
                    if es_listo_ext:
                        if registro_eis and registro_eis['estado'] in ['COMPLETADO', 'REPETIR']:
                            item['estado_db'] = registro_eis['estado']
                        else:
                            item['estado_db'] = 'LISTO_PARA_RECOGER'
                    elif registro_eis:
                        item['estado_db'] = registro_eis['estado']
                    else:
                        item['estado_db'] = 'PENDIENTE'

                    datos_totales.append(item)
                    stats['procesados_ok'] += 1

                except Exception as e:
                    stats['con_error'] += 1
                    error_msg = f"Fila {index} ({of_str}): {str(e)}"
                    if error_msg not in stats['errores_detalle']:
                        stats['errores_detalle'].append(error_msg)

        return JsonResponse({'status': 'ok', 'data': datos_totales, 'stats': stats})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
def api_registrar_actividad(request):
    if request.method == 'POST':
        try:
            # Al usar FormData con archivos, los datos vienen en request.POST, no en el body JSON
            if request.content_type.startswith('multipart/form-data'):
                data = request.POST
            else:
                data = json.loads(request.body)

            of_num = str(data.get('of')).strip().upper()
            proceso = data.get('proceso')
            tipo = data.get('tipo_registro')
            
            if not of_num or not proceso:
                return JsonResponse({'status': 'error', 'message': 'Faltan datos obligatorios'}, status=400)

            # Parsear fecha_programada
            fecha_prog_str = data.get('fecha_programada')
            fecha_prog_dt = None
            if fecha_prog_str and fecha_prog_str != '—':
                try:
                    from datetime import datetime
                    fecha_prog_dt = datetime.strptime(fecha_prog_str, '%d/%m/%Y').date()
                except:
                    pass

            from django.utils import timezone
            obj, created = OrdenFabricacion.objects.get_or_create(
                of=of_num, proceso=proceso,
                defaults={
                    'cliente': data.get('cliente', 'Desconocido'),
                    'descripcion': data.get('descripcion', '—'),
                    'referencia': data.get('referencia'),
                    'fecha_programada': fecha_prog_dt,
                    'fecha_registro': timezone.now().date(),
                    'maquina': data.get('maquina'),
                }
            )

            # Mantener la máquina al día (viene del Excel en cada sincronización),
            # incluso si el registro ya existía de antes.
            if data.get('maquina'):
                obj.maquina = data.get('maquina')

            bano_info = None
            if tipo == 'CREAR_FABRICACION':
                obj.responsables = data.get('responsable')
                obj.tiempo = data.get('tiempo')
                obj.peso_inicial = data.get('peso_i')
                obj.peso_final = data.get('peso_f')
                # Si la fila venia de REPETIR, descripcion quedo pisada con el
                # motivo del dano ("Fisico: REPETIR. ..."); al reactivar para
                # una nueva produccion hay que restaurar la descripcion real
                # del grabado (la que manda el Plani/Excel).
                if data.get('descripcion'):
                    obj.descripcion = data.get('descripcion')
                try:
                    pi = float(data.get('peso_i') or 0)  # g
                    pf = float(data.get('peso_f') or 0)  # g
                    obj.perdida = max(0, pi - pf)  # gramos
                except:
                    obj.perdida = 0
                obj.temp = data.get('temp'); obj.rpm = data.get('rpm'); obj.compensacion = data.get('compensacion')
                obj.compensacion_motivo = data.get('compensacion_motivo')
                obj.estado = 'EN_PROCESO'

                # Compensación de baño: se acumula en el contador único compartido
                # por todas las máquinas (ver EstadoBano). Cuando llega al límite
                # hay que avisarle al usuario que toca renovar el agua del baño.
                bano_ml = (obj.perdida / 1000) * 6.6 if obj.perdida else 0
                if bano_ml > 0:
                    estado_bano = EstadoBano.obtener()
                    estado_bano.ml_acumulados += bano_ml
                    estado_bano.save()
                    bano_info = {
                        'ml_acumulados': round(estado_bano.ml_acumulados, 1),
                        'limite': EstadoBano.LIMITE_ML,
                        'alerta': estado_bano.ml_acumulados >= EstadoBano.LIMITE_ML,
                    }
            
            elif tipo == 'ENVIAR_MAQUINA':
                obj.estado = 'EN_MAQUINA'
            
            elif tipo == 'REPORTE_DANO':
                obj.estado = 'REVISION'
                obj.descripcion = f"FALLO: {data.get('comentario')}"
                # Guardar foto si existe
                if 'foto_dano' in request.FILES:
                    obj.foto_dano = request.FILES['foto_dano']
            
            elif tipo == 'ALMACEN_RECOGER':
                obj.ubicacion = data.get('ubicacion'); obj.sobre = data.get('sobre')
                comentario = data.get('comentario', ''); estado_fisico = data.get('estado_fisico', '')
                if estado_fisico == 'REPETIR':
                    if not comentario or len(comentario.strip()) < 5:
                        return JsonResponse({'status': 'error', 'message': 'Comentario obligatorio para repetir'}, status=400)
                    obj.estado = 'REPETIR'
                else:
                    obj.estado = 'COMPLETADO'
                    obj.usos_acumulados = (obj.usos_acumulados or 0) + 1
                obj.descripcion = f"Físico: {estado_fisico}. {comentario}"

            obj.save()
            
            if request.user.is_authenticated:
                obj.usuario = request.user
                obj.save()

            respuesta = {'status': 'ok', 'message': 'Registro EIS actualizado'}
            if bano_info:
                respuesta['bano'] = bano_info
            return JsonResponse(respuesta)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

@login_required
def api_estado_bano(request):
    eb = EstadoBano.obtener()
    return JsonResponse({
        'ml_acumulados': round(eb.ml_acumulados, 1),
        'limite': EstadoBano.LIMITE_ML,
        'alerta': eb.ml_acumulados >= EstadoBano.LIMITE_ML,
        'ultima_renovacion': eb.ultima_renovacion.strftime('%d/%m/%Y %H:%M') if eb.ultima_renovacion else None,
        'renovado_por': eb.renovado_por,
    })

@login_required
def api_renovar_bano(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    from django.utils import timezone
    eb = EstadoBano.obtener()
    eb.ml_acumulados = 0
    eb.ultima_renovacion = timezone.now()
    eb.renovado_por = request.user.username
    eb.save()
    return JsonResponse({'status': 'ok', 'message': 'Baño renovado. Contador reiniciado.'})

@login_required
def confirmar_sincronizacion(request):
    if request.method == 'POST':
        try:
            body = json.loads(request.body)
            datos = body.get('datos', [])
            creados = 0; actualizados = 0
            for row in datos:
                if not row.get('of') or row['of'] == '—': continue
                obj, created = OrdenFabricacion.objects.update_or_create(
                    of=str(row['of']), proceso=row.get('proceso', 'General'),
                    defaults={
                        'referencia': row.get('referencia'), 'descripcion': row.get('descripcion', '—'),
                        'cliente': row.get('cliente', 'Desconocido'), 'horas_proceso': row.get('horas_proceso'),
                        'responsable': row.get('responsable'),
                    }
                )
                if created: creados += 1
                else: actualizados += 1
            return JsonResponse({'status': 'ok', 'message': f'Sincronizados: {creados + actualizados}'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

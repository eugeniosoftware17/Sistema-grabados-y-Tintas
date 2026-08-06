import pandas as pd
import json
import os
import re
import io
from django.shortcuts import render
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from .models import OrdenFabricacion
from django.db import connections

def buscar_datos_externos(of_numero, proceso):
    """Busca información técnica en la base de datos CigarRings2012 usando G_orden"""
    try:
        # Normalizar OF para búsqueda numérica: "22651.0" -> "22651"
        of_str = str(of_numero).strip()
        if '.' in of_str:
            of_str = of_str.split('.')[0]
        of_limpia = re.sub(r'\D', '', of_str)
        
        if not of_limpia:
            return {'encontrado_ext': False, 'maquina_ext': '—', 'sobre_ext': '—', 'ref_ext': '—', 'acabado_ext': '0'}

        of_int = int(of_limpia)

        with connections['externa_2012'].cursor() as cursor:
            if proceso == 'STAMPING':
                col_maq = 'PRO_stamping_Maquina'
                col_of_ref = 'OF_Stamping'
                col_acabado = 'Acabat_Stamping'
            else:
                col_maq = 'PRO_Embossing'
                col_of_ref = 'OF_Embossing'
                col_acabado = 'Acabat_Embossing'
            
            query = f"SELECT {col_maq}, Sobre_pelicula, {col_of_ref}, {col_acabado} FROM CigarRings2012 WHERE G_orden = %s"
            cursor.execute(query, [of_int])
            row = cursor.fetchone()
            
            if row:
                val_acabado = str(row[3]).strip() if row[3] is not None else '0'
                if val_acabado.lower() in ['true', '1', '1.0', 'ok', 's', 'y']:
                    val_acabado = '1'
                else:
                    val_acabado = '0'

                return {
                    'maquina_ext': row[0] if row[0] else '—',
                    'sobre_ext': row[1] if row[1] else '—',
                    'ref_ext': row[2] if row[2] else '—',
                    'acabado_ext': val_acabado,
                    'encontrado_ext': True
                }
    except Exception as e:
        print(f"Error consultando DB externa para OF {of_numero}: {e}")
    return {'encontrado_ext': False, 'maquina_ext': '—', 'sobre_ext': '—', 'ref_ext': '—', 'acabado_ext': '0'}

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

@csrf_exempt
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
                'fecha_programada': ['FECHA STAMPING', 'FECHA EMBOSSING', 'FECHA', 'DATE', 'FECHA PROG.']
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
                    info_ext = buscar_datos_externos(of_str, nombre_hoja)
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

@csrf_exempt
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
                    'fecha_registro': timezone.now().date()
                }
            )

            if tipo == 'CREAR_FABRICACION':
                obj.responsables = data.get('responsable')
                obj.tiempo = data.get('tiempo')
                obj.peso_inicial = data.get('peso_i')
                obj.peso_final = data.get('peso_f')
                try:
                    pi = float(data.get('peso_i') or 0)
                    pf = float(data.get('peso_f') or 0)
                    obj.perdida = max(0, pi - pf)
                except:
                    obj.perdida = 0
                obj.temp = data.get('temp'); obj.rpm = data.get('rpm'); obj.compensacion = data.get('compensacion')
                obj.estado = 'EN_PROCESO'
            
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

            return JsonResponse({'status': 'ok', 'message': 'Registro EIS actualizado'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

@csrf_exempt
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

from django.urls import path
from . import views
from . import views_fabricacion
from . import views_grabados


app_name = 'grabados'

urlpatterns = [
    # Vistas de Tablas
    path('consulta/', views.grabado_consulta, name='grabado_consulta'),
    path('plani/', views.plani_consulta, name='plani_consulta'),
    path('estadisticas/', views.grabado_estadisticas, name='grabado_estadisticas'),

    # Endpoints de API
    path('api/registros/', views.api_obtener_registros, name='api_registros'),
    path('api/kpis/', views.api_dashboard_kpis, name='api_kpis'),
    path('api/estadisticas/', views.api_estadisticas_detalladas, name='api_stats_detalladas'),
    path('api/sincronizar/', views.sincronizar_plani, name='api_sincronizar'),
    path('api/confirmar/', views.confirmar_sincronizacion, name='api_confirmar'),
    path('api/registrar/', views.api_registrar_actividad, name='api_registrar'),
    path('api/historial/<str:of_numero>/', views.api_historial_orden, name='api_historial'),
    path('api/eliminar/', views.api_eliminar_registro, name='api_eliminar'),
    path('api/bano/', views.api_estado_bano, name='api_estado_bano'),
    path('api/bano/renovar/', views.api_renovar_bano, name='api_renovar_bano'),

    # --- Alta manual "Fabricación" (TEMPORAL, ver views_fabricacion.py) ---
    path('fabricacion/', views_fabricacion.fabricacion, name='fabricacion'),
    path('api/fabricacion/buscar-externo/', views_fabricacion.api_buscar_externo, name='api_fabricacion_buscar_externo'),
    path('api/fabricacion/registrar/', views_fabricacion.api_registrar_manual, name='api_fabricacion_registrar'),
    path('api/fabricacion/listar/', views_fabricacion.api_listar_manual, name='api_fabricacion_listar'),
    path('api/fabricacion/registrar-lote/', views_fabricacion.api_registrar_lote, name='api_fabricacion_registrar_lote'),
    path('api/fabricacion/editar-ubicacion/', views_fabricacion.api_editar_ubicacion, name='api_fabricacion_editar_ubicacion'),

    # --- Grabado como entidad propia (fase 2, ver views_grabados.py) ---
    path('alta/', views_grabados.alta_grabado, name='alta_grabado'),
    path('api/alta/buscar/', views_grabados.api_alta_buscar, name='api_alta_buscar'),
    path('api/alta/registrar/', views_grabados.api_alta_registrar, name='api_alta_registrar'),
    path('k1/', views_grabados.k1_pendientes, name='k1_pendientes'),
    path('api/k1/pendientes/', views_grabados.api_k1_pendientes, name='api_k1_pendientes'),
    path('api/k1/<int:prueba_id>/aprobar/', views_grabados.api_k1_aprobar, name='api_k1_aprobar'),
    path('api/k1/<int:prueba_id>/rechazar/', views_grabados.api_k1_rechazar, name='api_k1_rechazar'),
    path('api/grabado/<int:grabado_id>/detalle/', views_grabados.api_grabado_detalle, name='api_grabado_detalle'),
    path('inventario/', views_grabados.inventario_grabados, name='inventario_grabados'),
    path('api/inventario/', views_grabados.api_inventario, name='api_inventario'),
]
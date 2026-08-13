from django.urls import path
from . import views
from . import views_fabricacion


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

    # --- Alta manual "Fabricación" (TEMPORAL, ver views_fabricacion.py) ---
    path('fabricacion/', views_fabricacion.fabricacion, name='fabricacion'),
    path('api/fabricacion/buscar-externo/', views_fabricacion.api_buscar_externo, name='api_fabricacion_buscar_externo'),
    path('api/fabricacion/registrar/', views_fabricacion.api_registrar_manual, name='api_fabricacion_registrar'),
    path('api/fabricacion/listar/', views_fabricacion.api_listar_manual, name='api_fabricacion_listar'),
]
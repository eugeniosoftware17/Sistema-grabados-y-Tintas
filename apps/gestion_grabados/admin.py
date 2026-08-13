from django.contrib import admin
from .models import OrdenFabricacion, EstadoBano

@admin.register(EstadoBano)
class EstadoBanoAdmin(admin.ModelAdmin):
    list_display = ('ml_acumulados', 'ultima_renovacion', 'renovado_por', 'actualizado_el')
    readonly_fields = ('actualizado_el',)

@admin.register(OrdenFabricacion)
class OrdenFabricacionAdmin(admin.ModelAdmin):
    # Columnas que se verán en la lista principal
    list_display = ('of', 'cliente', 'proceso', 'estado', 'ubicacion', 'fecha_programada', 'fecha_registro', 'actualizado_el')
    
    # Filtros laterales
    list_filter = ('proceso', 'estado', 'fecha_programada', 'fecha_registro')
    
    # Buscador (OF y Cliente)
    search_fields = ('of', 'cliente', 'descripcion')
    
    # Organización de los campos al editar
    fieldsets = (
        ('Información General (Excel)', {
            'fields': ('of', 'referencia', 'cliente', 'descripcion', 'proceso', 'maquina', 'fecha_programada')
        }),
        ('Logística y Papel', {
            'fields': ('cantidad_formatos', 'horas_proceso', 'papel')
        }),
        ('Gestión en Planta (EIS)', {
            'fields': ('estado', 'ubicacion', 'sobre', 'responsables', 'fecha_registro')
        }),
        ('Parámetros Técnicos', {
            'fields': ('peso_inicial', 'peso_final', 'perdida', 'temp', 'rpm', 'tiempo', 'compensacion', 'compensacion_motivo'),
            'classes': ('collapse',) # Esta sección se puede contraer
        }),
    )

    # Campos de solo lectura (opcional, para auditoría)
    readonly_fields = ('creado_el', 'actualizado_el')
    
    # Orden por defecto (las más nuevas primero)
    ordering = ('-actualizado_el',)

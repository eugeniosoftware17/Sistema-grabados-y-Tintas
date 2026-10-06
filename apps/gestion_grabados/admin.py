from django.contrib import admin
from .models import (
    OrdenFabricacion, EstadoBano,
    Maquina, Grabado, FabricacionGrabado, PruebaK1, EnvioMaquina, LegadoOrden,
)

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


# ============================================================
# REESTRUCTURACIÓN (fase 1): grabado como entidad propia
# ============================================================

@admin.register(Maquina)
class MaquinaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'activa')
    list_filter = ('activa',)
    search_fields = ('nombre',)


class FabricacionGrabadoInline(admin.TabularInline):
    model = FabricacionGrabado
    extra = 0
    fields = ('numero', 'tipo', 'responsables', 'peso_inicial', 'peso_final', 'perdida',
              'temp', 'rpm', 'tiempo', 'compensacion', 'registrado_por', 'registrado_el')
    show_change_link = True


class PruebaK1Inline(admin.TabularInline):
    model = PruebaK1
    extra = 0
    fields = ('intento', 'fabricacion', 'maquina', 'resultado', 'motivo_rechazo',
              'decidido_por', 'decidido_el')
    show_change_link = True


class EnvioMaquinaInline(admin.TabularInline):
    model = EnvioMaquina
    extra = 0
    fields = ('of', 'maquina', 'enviado_el', 'recogido_el', 'estado_fisico', 'ubicacion')
    show_change_link = True


@admin.register(Grabado)
class GrabadoAdmin(admin.ModelAdmin):
    list_display = ('of_origen', 'proceso', 'cliente', 'estado', 'ubicacion',
                    'usos_acumulados', 'aprobado_legado', 'actualizado_el')
    list_filter = ('proceso', 'estado', 'aprobado_legado', 'datos_manuales')
    search_fields = ('of_origen', 'cliente', 'referencia', 'sobre')
    readonly_fields = ('creado_por', 'creado_el', 'actualizado_el')
    inlines = [FabricacionGrabadoInline, PruebaK1Inline, EnvioMaquinaInline]


@admin.register(FabricacionGrabado)
class FabricacionGrabadoAdmin(admin.ModelAdmin):
    list_display = ('grabado', 'numero', 'tipo', 'responsables', 'registrado_por', 'registrado_el',
                    'revisar_pesos')
    list_filter = ('tipo', 'grabado__proceso', 'revisar_pesos')
    search_fields = ('grabado__of_origen', 'responsables')
    list_select_related = ('grabado', 'registrado_por')


@admin.register(PruebaK1)
class PruebaK1Admin(admin.ModelAdmin):
    list_display = ('grabado', 'intento', 'maquina', 'resultado', 'decidido_por', 'decidido_el')
    list_filter = ('resultado', 'maquina')
    search_fields = ('grabado__of_origen',)
    readonly_fields = ('creado_por', 'creado_el')
    list_select_related = ('grabado', 'maquina', 'decidido_por')


@admin.register(EnvioMaquina)
class EnvioMaquinaAdmin(admin.ModelAdmin):
    list_display = ('of', 'grabado', 'maquina', 'enviado_el', 'recogido_el', 'estado_fisico')
    list_filter = ('maquina', 'estado_fisico')
    search_fields = ('of', 'grabado__of_origen')
    list_select_related = ('grabado', 'maquina')


@admin.register(LegadoOrden)
class LegadoOrdenAdmin(admin.ModelAdmin):
    list_display = ('orden', 'rol', 'grabado', 'fabricacion', 'envio', 'migrado_el')
    list_filter = ('rol',)
    search_fields = ('orden__of', 'grabado__of_origen')
    readonly_fields = ('orden', 'rol', 'grabado', 'fabricacion', 'envio', 'migrado_el')
    list_select_related = ('orden', 'grabado')

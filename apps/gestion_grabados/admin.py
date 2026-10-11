import json

from django import forms
from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from . import selectors
from .services import etiquetas
from .models import (
    OrdenFabricacion, EstadoBano,
    Maquina, Grabado, FabricacionGrabado, PruebaK1, EnvioMaquina, LegadoOrden,
    HistorialUbicacion, HistorialActivacion, Sobre, HistorialUbicacionSobre, HistorialActivacionSobre,
    ConfiguracionEtiqueta,
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
                    'usos_acumulados', 'tipo', 'actualizado_el')
    list_filter = ('proceso', 'estado', 'tipo', 'datos_manuales')
    search_fields = ('of_origen', 'cliente', 'referencia', 'sobre')
    # La ubicación solo cambia por services.grabados.cambiar_ubicacion (con historial) y la
    # desactivación por services.grabados.desactivar_grabado / reactivar_grabado.
    readonly_fields = ('ubicacion', 'sobre_asociado', 'desactivado_por', 'desactivado_el',
                       'motivo_desactivacion', 'estado_previo', 'creado_por', 'creado_el', 'actualizado_el')
    inlines = [FabricacionGrabadoInline, PruebaK1Inline, EnvioMaquinaInline]

    def has_delete_permission(self, request, obj=None):
        # Un grabado nunca se elimina: se desactiva desde su panel de detalle.
        return False


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


@admin.register(HistorialUbicacion)
class HistorialUbicacionAdmin(admin.ModelAdmin):
    """Solo lectura: lo escribe services.grabados.cambiar_ubicacion."""
    list_display = ('grabado', 'ubicacion_anterior', 'ubicacion_nueva', 'origen', 'usuario', 'cambiado_el')
    list_filter = ('origen',)
    search_fields = ('grabado__of_origen', 'ubicacion_anterior', 'ubicacion_nueva')
    list_select_related = ('grabado', 'usuario')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(HistorialActivacion)
class HistorialActivacionAdmin(admin.ModelAdmin):
    """Solo lectura: lo escriben services.grabados.desactivar_grabado / reactivar_grabado."""
    list_display = ('grabado', 'accion', 'estado', 'motivo', 'usuario', 'fecha')
    list_filter = ('accion',)
    search_fields = ('grabado__of_origen', 'motivo')
    list_select_related = ('grabado', 'usuario')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SoloLecturaAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Sobre)
class SobreAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'of_origen', 'proceso', 'ubicacion', 'estado', 'actualizado_el')
    list_filter = ('proceso', 'estado')
    search_fields = ('of_origen', 'codigo', 'ubicacion')
    # La ubicación solo cambia por services.sobres.cambiar_ubicacion_sobre (con historial)
    # y la desactivación por services.sobres.desactivar_sobre / reactivar_sobre.
    readonly_fields = ('ubicacion', 'estado', 'desactivado_por', 'desactivado_el', 'motivo_desactivacion',
                       'creado_por', 'creado_el', 'actualizado_el')

    def has_delete_permission(self, request, obj=None):
        # Un sobre nunca se elimina: se desactiva desde la pantalla Sobres.
        return False


@admin.register(HistorialUbicacionSobre)
class HistorialUbicacionSobreAdmin(SoloLecturaAdmin):
    """Solo lectura: lo escribe services.sobres.cambiar_ubicacion_sobre."""
    list_display = ('sobre', 'ubicacion_anterior', 'ubicacion_nueva', 'origen', 'usuario', 'cambiado_el')
    list_filter = ('origen',)
    search_fields = ('sobre__of_origen', 'sobre__codigo', 'ubicacion_anterior', 'ubicacion_nueva')
    list_select_related = ('sobre', 'usuario')


@admin.register(HistorialActivacionSobre)
class HistorialActivacionSobreAdmin(SoloLecturaAdmin):
    """Solo lectura: lo escriben services.sobres.desactivar_sobre / reactivar_sobre."""
    list_display = ('sobre', 'accion', 'motivo', 'usuario', 'fecha')
    list_filter = ('accion',)
    search_fields = ('sobre__of_origen', 'sobre__codigo', 'motivo')
    list_select_related = ('sobre', 'usuario')


@admin.register(LegadoOrden)
class LegadoOrdenAdmin(admin.ModelAdmin):
    list_display = ('orden', 'rol', 'grabado', 'fabricacion', 'envio', 'migrado_el')
    list_filter = ('rol',)
    search_fields = ('orden__of', 'grabado__of_origen')
    readonly_fields = ('orden', 'rol', 'grabado', 'fabricacion', 'envio', 'migrado_el')
    list_select_related = ('orden', 'grabado')


# ============================================================
# Diseñador de la etiqueta 6x4 (una sola fila, solo superusuario)
# ============================================================

class ConfiguracionEtiquetaForm(forms.ModelForm):
    """El diseño viaja como JSON en un campo oculto que llena disenador_etiqueta.js;
    el logo se sube como archivo y se guarda en la base (services.etiquetas)."""
    campos_json = forms.CharField(widget=forms.HiddenInput)
    logo_archivo = forms.FileField(required=False, label="Logo (PNG o JPG, máx. 1 MB)",
                                   widget=forms.FileInput(attrs={'accept': 'image/png,image/jpeg'}))
    quitar_logo = forms.BooleanField(required=False, label="Volver al logo por defecto")

    class Meta:
        model = ConfiguracionEtiqueta
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            campos = etiquetas.normalizar_campos(self.instance.campos)
        except etiquetas.ConfiguracionInvalida:
            campos = etiquetas.normalizar_campos(None)
        self.fields['campos_json'].initial = json.dumps(campos)

    def clean_campos_json(self):
        try:
            return etiquetas.normalizar_campos(json.loads(self.cleaned_data['campos_json']))
        except json.JSONDecodeError:
            raise forms.ValidationError('El diseño de la etiqueta no tiene un formato válido.')
        except etiquetas.ConfiguracionInvalida as e:
            raise forms.ValidationError(str(e))

    def clean_logo_archivo(self):
        archivo = self.cleaned_data.get('logo_archivo')
        if not archivo:
            return None
        if archivo.size > etiquetas.LOGO_MAXIMO_BYTES:
            raise forms.ValidationError('El logo no puede pesar más de 1 MB.')
        contenido = archivo.read()
        try:
            etiquetas.validar_logo(contenido)
        except etiquetas.ConfiguracionInvalida as e:
            raise forms.ValidationError(str(e))
        return contenido


@admin.register(ConfiguracionEtiqueta)
class ConfiguracionEtiquetaAdmin(admin.ModelAdmin):
    """
    Una sola fila (pk=1): la lista lleva directo al diseñador. Si todavía no existe,
    el diseñador abre con los valores por defecto y la fila se crea al guardar.
    """
    form = ConfiguracionEtiquetaForm
    change_form_template = 'admin/gestion_grabados/configuracionetiqueta/change_form.html'

    def _es_superusuario(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_module_permission(self, request):
        return self._es_superusuario(request)

    def has_view_permission(self, request, obj=None):
        return self._es_superusuario(request)

    def has_change_permission(self, request, obj=None):
        return self._es_superusuario(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_object(self, request, object_id, from_field=None):
        if str(object_id) != '1':
            return None
        return ConfiguracionEtiqueta.objects.filter(pk=1).first() or ConfiguracionEtiqueta(pk=1)

    def changelist_view(self, request, extra_context=None):
        if not self.has_change_permission(request):
            return super().changelist_view(request, extra_context)   # 403 de Django
        opciones = self.model._meta
        return HttpResponseRedirect(
            reverse(f'admin:{opciones.app_label}_{opciones.model_name}_change', args=[1]))

    def render_change_form(self, request, context, *args, **kwargs):
        # La vista previa sale con el diseño guardado; disenador_etiqueta.js la vuelve a
        # acomodar con el campo oculto (así un POST con error no pierde lo movido).
        context.update({
            'config': selectors.configuracion_etiqueta(),
            'datos': selectors.datos_etiqueta_prueba(),
            'disenador': True,
            'campos_por_defecto': etiquetas.CAMPOS_POR_DEFECTO,
            'nombres_campos': etiquetas.NOMBRES_CAMPOS,
            'limites': {'ancho': etiquetas.ANCHO_ETIQUETA, 'alto': etiquetas.ALTO_ETIQUETA,
                        'tamano_min': etiquetas.TAMANO_MINIMO, 'tamano_max': etiquetas.TAMANO_MAXIMO,
                        'medida_min': etiquetas.MEDIDA_MINIMA},
        })
        return super().render_change_form(request, context, *args, **kwargs)

    def save_model(self, request, obj, form, change):
        config = etiquetas.guardar_configuracion(
            campos=form.cleaned_data['campos_json'],
            usuario=request.user,
            logo=form.cleaned_data.get('logo_archivo'),
            quitar_logo=form.cleaned_data.get('quitar_logo', False),
        )
        obj.pk = config.pk

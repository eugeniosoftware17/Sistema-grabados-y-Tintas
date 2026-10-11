"""
Etiqueta 6x4" con código de barras Code 128 para la impresora Zebra.

La página se imprime desde el navegador con CSS al tamaño exacto (@page 6in 4in).
Con ?imprimir=1 abre el diálogo de impresión al cargar: así la usa
static/js/componentes/etiqueta.js dentro de un iframe oculto. No se guarda ningún
control de impresiones. El diseño sale de ConfiguracionEtiqueta (admin, solo
superusuario). El permiso del módulo Grabados lo verifica urls.py (proteger_urls).
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_sameorigin

from . import selectors


def _render_etiqueta(request, datos, prueba=False):
    return render(request, 'etiqueta.html', {
        'datos': datos,
        'config': selectors.configuracion_etiqueta(),
        'imprimir': request.GET.get('imprimir') == '1',
        'prueba': prueba,
    })


@login_required
@xframe_options_sameorigin
def etiqueta_grabado(request, grabado_id):
    datos = selectors.datos_etiqueta_grabado(grabado_id)
    if datos is None:
        raise Http404('El grabado no existe.')
    return _render_etiqueta(request, datos)


@login_required
@xframe_options_sameorigin
def etiqueta_sobre(request, sobre_id):
    datos = selectors.datos_etiqueta_sobre(sobre_id)
    if datos is None:
        raise Http404('El sobre no existe.')
    return _render_etiqueta(request, datos)


@login_required
@xframe_options_sameorigin
def etiqueta_prueba(request):
    """Página de prueba de impresión: datos de ejemplo con el diseño vigente."""
    return _render_etiqueta(request, selectors.datos_etiqueta_prueba(), prueba=True)

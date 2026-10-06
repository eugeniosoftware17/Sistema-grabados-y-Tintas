"""
{% static_v 'js/archivo.js' %}: igual que {% static %}, pero agrega
?v=<fecha de modificación del archivo>. Así el navegador baja el JS/CSS nuevo
apenas cambia, en vez de seguir usando la copia que tiene en caché (con
DEBUG=True Django sirve los estáticos sin hash en el nombre).
"""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def static_v(ruta):
    url = static(ruta)
    archivo = finders.find(ruta)
    if archivo:
        url += f'?v={int(os.path.getmtime(archivo))}'
    return url

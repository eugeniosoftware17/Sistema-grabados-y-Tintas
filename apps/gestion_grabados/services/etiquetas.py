"""
Reglas del diseño de la etiqueta 6x4" (ConfiguracionEtiqueta, una sola fila).

Lo edita el superusuario en el admin; aquí se valida y se guarda. No se guarda
ningún control de impresiones.
"""
import io

from django.db import transaction

from ..models import ConfiguracionEtiqueta

ANCHO_ETIQUETA = 6.0   # pulgadas (horizontal)
ALTO_ETIQUETA = 4.0
TAMANO_MINIMO, TAMANO_MAXIMO = 6, 96   # pt
MEDIDA_MINIMA = 0.1                    # pulgadas
LOGO_MAXIMO_BYTES = 1024 * 1024
FORMATOS_LOGO = {'PNG': 'image/png', 'JPEG': 'image/jpeg'}

# Orden en que se dibujan y se listan en el diseñador.
CAMPOS_ETIQUETA = [
    ('logo', 'Logo'),
    ('of', 'OF'),
    ('proceso', 'Proceso'),
    ('cliente', 'Cliente'),
    ('descripcion', 'Descripción'),
    ('ubicacion', 'Ubicación'),
    ('fecha', 'Última modificación'),
    ('codigo_barras', 'Código de barras'),
]
NOMBRES_CAMPOS = dict(CAMPOS_ETIQUETA)

# x, y, ancho y alto en pulgadas desde la esquina superior izquierda; tamano en pt
# (en el logo no se usa; en el código de barras es el texto que va debajo).
CAMPOS_POR_DEFECTO = {
    'logo':          {'visible': True, 'x': 0.2, 'y': 0.15, 'ancho': 1.7, 'alto': 0.8, 'tamano': 10},
    'of':            {'visible': True, 'x': 2.1, 'y': 0.15, 'ancho': 3.7, 'alto': 0.6, 'tamano': 30},
    'proceso':       {'visible': True, 'x': 2.1, 'y': 0.75, 'ancho': 3.7, 'alto': 0.35, 'tamano': 16},
    'cliente':       {'visible': True, 'x': 0.2, 'y': 1.1, 'ancho': 5.6, 'alto': 0.35, 'tamano': 14},
    'descripcion':   {'visible': True, 'x': 0.2, 'y': 1.45, 'ancho': 5.6, 'alto': 0.5, 'tamano': 11},
    'ubicacion':     {'visible': True, 'x': 0.2, 'y': 1.95, 'ancho': 3.6, 'alto': 0.8, 'tamano': 44},
    'fecha':         {'visible': True, 'x': 3.9, 'y': 2.3, 'ancho': 1.9, 'alto': 0.45, 'tamano': 9},
    'codigo_barras': {'visible': True, 'x': 0.2, 'y': 2.85, 'ancho': 5.6, 'alto': 1.0, 'tamano': 11},
}


class ConfiguracionInvalida(ValueError):
    pass


def _numero(valor, clave, propiedad):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ConfiguracionInvalida(f'{NOMBRES_CAMPOS[clave]}: "{propiedad}" tiene que ser un número.')
    return float(valor)


def normalizar_campos(campos):
    """
    Valida el diseño y lo devuelve completo: los campos que faltan toman el valor
    por defecto, las medidas se redondean a centésimas y la posición se ajusta para
    que el campo no se salga de la etiqueta. Un campo desconocido o un valor fuera
    de rango da ConfiguracionInvalida.
    """
    if campos is None:
        campos = {}
    if not isinstance(campos, dict):
        raise ConfiguracionInvalida('El diseño de la etiqueta no tiene un formato válido.')
    desconocidos = set(campos) - set(NOMBRES_CAMPOS)
    if desconocidos:
        raise ConfiguracionInvalida(f'Campo desconocido en la etiqueta: {", ".join(sorted(desconocidos))}.')

    resultado = {}
    for clave, nombre in CAMPOS_ETIQUETA:
        valor = {**CAMPOS_POR_DEFECTO[clave], **(campos.get(clave) or {})}
        if not isinstance(valor['visible'], bool):
            raise ConfiguracionInvalida(f'{nombre}: "visible" tiene que ser verdadero o falso.')
        ancho = round(_numero(valor['ancho'], clave, 'ancho'), 2)
        alto = round(_numero(valor['alto'], clave, 'alto'), 2)
        tamano = round(_numero(valor['tamano'], clave, 'tamano'), 1)
        if not MEDIDA_MINIMA <= ancho <= ANCHO_ETIQUETA:
            raise ConfiguracionInvalida(f'{nombre}: el ancho tiene que estar entre {MEDIDA_MINIMA} y '
                                        f'{ANCHO_ETIQUETA:g} pulgadas.')
        if not MEDIDA_MINIMA <= alto <= ALTO_ETIQUETA:
            raise ConfiguracionInvalida(f'{nombre}: el alto tiene que estar entre {MEDIDA_MINIMA} y '
                                        f'{ALTO_ETIQUETA:g} pulgadas.')
        if not TAMANO_MINIMO <= tamano <= TAMANO_MAXIMO:
            raise ConfiguracionInvalida(f'{nombre}: el tamaño de letra tiene que estar entre '
                                        f'{TAMANO_MINIMO} y {TAMANO_MAXIMO} pt.')
        x = round(_numero(valor['x'], clave, 'x'), 2)
        y = round(_numero(valor['y'], clave, 'y'), 2)
        resultado[clave] = {
            'visible': valor['visible'],
            'x': round(min(max(x, 0.0), ANCHO_ETIQUETA - ancho), 2),
            'y': round(min(max(y, 0.0), ALTO_ETIQUETA - alto), 2),
            'ancho': ancho,
            'alto': alto,
            'tamano': tamano,
        }
    return resultado


def validar_logo(contenido):
    """Devuelve el tipo (image/png o image/jpeg) de un logo válido; si no, ConfiguracionInvalida."""
    from PIL import Image, UnidentifiedImageError

    if not contenido:
        raise ConfiguracionInvalida('El archivo del logo está vacío.')
    if len(contenido) > LOGO_MAXIMO_BYTES:
        raise ConfiguracionInvalida('El logo no puede pesar más de 1 MB.')
    try:
        with Image.open(io.BytesIO(contenido)) as imagen:
            formato = imagen.format
            imagen.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ConfiguracionInvalida('El logo tiene que ser una imagen PNG o JPG.')
    if formato not in FORMATOS_LOGO:
        raise ConfiguracionInvalida('El logo tiene que ser una imagen PNG o JPG.')
    return FORMATOS_LOGO[formato]


def guardar_configuracion(*, campos, usuario, logo=None, quitar_logo=False):
    """
    Guarda el diseño (y, si viene, el logo nuevo en la base). quitar_logo vuelve
    al logo por defecto de static/. Devuelve la ConfiguracionEtiqueta.
    """
    campos = normalizar_campos(campos)
    tipo_logo = validar_logo(logo) if logo is not None else None
    with transaction.atomic():
        ConfiguracionEtiqueta.objects.get_or_create(pk=1)
        config = ConfiguracionEtiqueta.objects.select_for_update().get(pk=1)
        config.campos = campos
        if tipo_logo:
            config.logo = logo
            config.logo_tipo = tipo_logo
        elif quitar_logo:
            config.logo = None
            config.logo_tipo = ''
        config.actualizado_por = usuario
        config.save()
    return config

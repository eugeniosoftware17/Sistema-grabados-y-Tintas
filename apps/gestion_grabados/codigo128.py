"""
Código de barras Code 128 (juego B) sin dependencias: lo dibuja el servidor como
SVG para la etiqueta, así no hace falta ninguna librería externa ni internet.

Juego B cubre ASCII 32-126 (dígitos, letras, guion...), suficiente para "22741-S".
"""
from django.utils.html import escape

# Anchos de barra/espacio de cada símbolo (valor 0..105) y del Stop (106).
# Cada símbolo suma 11 módulos; el Stop, 13.
PATRONES = [
    '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
    '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
    '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
    '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
    '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
    '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
    '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
    '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
    '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
    '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
    '114131', '311141', '411131', '211412', '211214', '211232', '2331112',
]
INICIO_B = 104
STOP = 106
ZONA_SILENCIO = 10   # módulos en blanco a cada lado (mínimo del estándar)


class CodigoInvalido(ValueError):
    pass


def valores(texto):
    """Valores del juego B de cada carácter, con Start B y dígito de control."""
    if not texto:
        raise CodigoInvalido('El código de barras no puede estar vacío.')
    datos = []
    for caracter in texto:
        codigo = ord(caracter)
        if not 32 <= codigo <= 126:
            raise CodigoInvalido(f'El carácter "{caracter}" no se puede codificar en Code 128.')
        datos.append(codigo - 32)
    control = (INICIO_B + sum(v * i for i, v in enumerate(datos, start=1))) % 103
    return [INICIO_B, *datos, control, STOP]


def modulos(texto):
    """Lista de anchos alternando barra, espacio, barra... (sin zonas de silencio)."""
    return [int(ancho) for valor in valores(texto) for ancho in PATRONES[valor]]


def svg(texto):
    """SVG que estira las barras a la caja que lo contiene (ancho y alto al 100%)."""
    anchos = modulos(texto)
    total = sum(anchos) + 2 * ZONA_SILENCIO
    x = ZONA_SILENCIO
    barras = []
    for i, ancho in enumerate(anchos):
        if i % 2 == 0:
            barras.append(f'<rect x="{x}" y="0" width="{ancho}" height="1"/>')
        x += ancho
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} 1" preserveAspectRatio="none" '
            f'shape-rendering="crispEdges" width="100%" height="100%" role="img" aria-label="{escape(texto)}">'
            f'{"".join(barras)}</svg>')

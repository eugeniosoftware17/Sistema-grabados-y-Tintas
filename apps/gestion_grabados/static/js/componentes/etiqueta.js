/* Imprime la etiqueta 6x4 de un grabado o un sobre (ver views_etiquetas.py).

   Carga la página de la etiqueta con ?imprimir=1 en un iframe oculto: la página
   abre el diálogo de impresión al cargar. Un iframe (y no una ventana nueva) para
   que el navegador no lo bloquee cuando se imprime después de un fetch, como al
   recoger en el PLANI. No se guarda ningún control de impresiones.

   Uso: Etiqueta.imprimirGrabado(id) / Etiqueta.imprimirSobre(id).
   BOTON_SVG: ícono de impresora para el botón de cada fila (cada pantalla arma
   su botón y escucha el clic). */
(function () {
    'use strict';

    let marco = null;

    function imprimir(url) {
        if (marco) marco.remove();
        marco = document.createElement('iframe');
        marco.title = 'Etiqueta para imprimir';
        marco.setAttribute('aria-hidden', 'true');
        marco.tabIndex = -1;
        // Fuera de la pantalla pero con el tamaño de la etiqueta (display:none no imprime bien).
        marco.style.cssText = 'position:fixed; left:-10000px; top:0; width:6in; height:4in; border:0;';
        marco.src = `${url}?imprimir=1`;
        document.body.appendChild(marco);
    }

    const BOTON_SVG = `<svg viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="6 9 6 2 18 2 18 9"/>
        <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>
        <rect x="6" y="14" width="12" height="8"/></svg>`;

    window.Etiqueta = {
        imprimirGrabado: (id) => imprimir(`/grabados/etiqueta/grabado/${encodeURIComponent(id)}/`),
        imprimirSobre: (id) => imprimir(`/grabados/etiqueta/sobre/${encodeURIComponent(id)}/`),
        BOTON_SVG,
    };
})();

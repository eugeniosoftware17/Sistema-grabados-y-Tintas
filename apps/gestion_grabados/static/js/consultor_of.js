/* ============================================================
   Consultor de OF — ver views_grabados.consultor_of
   Solo lectura: muestra lo que la API tiene de una OF y, por proceso,
   el grabado de EIS que le toca. El detalle del grabado se abre con el
   componente PanelGrabado (static/js/componentes/panel_grabado.js).
   ============================================================ */

(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    let ofActual = null;

    // Vacío -> "—" (la celda queda con rayita).
    function escapar(valor) {
        const div = document.createElement('div');
        div.textContent = valor === null || valor === undefined || valor === '' ? '—' : String(valor);
        return div.innerHTML;
    }

    function mostrarAviso(texto, tipo) {
        const aviso = $('cof-aviso');
        aviso.className = `aviso aviso--${tipo}`;
        aviso.textContent = texto;
        aviso.style.display = '';
    }

    function ocultarAviso() {
        $('cof-aviso').style.display = 'none';
    }

    function etiqueta(codigo, texto) {
        return `<span class="pg-etiqueta pg-etiqueta--${escapar(codigo)}">${escapar(texto)}</span>`;
    }

    function celdaAcabado(p) {
        if (!p.acabado) return escapar(null);
        return escapar(p.acabado) + (p.terminada ? ' <small>(terminada en planta)</small>' : '');
    }

    function celdaGrabado(p) {
        const notas = [];
        if (p.sin_proceso_en_api) notas.push(`La API no tiene ${p.proceso} para esta OF.`);
        else if (p.usa_grabado_de_otra) notas.push(`Usa el grabado de la OF ${p.of_origen}.`);
        const nota = notas.map((n) => `<span class="cof-nota">${escapar(n)}</span>`).join('');

        const g = p.grabado;
        if (!g) {
            // Con la API sin este proceso, Crear Grabado lo bloquearía: no se ofrece el enlace.
            const enlace = p.sin_proceso_en_api ? '' :
                `<a class="boton boton--detalle" href="/grabados/alta/?${new URLSearchParams({ of: p.of_origen, proceso: p.proceso })}">Ir a Crear Grabado</a>`;
            return `<div class="cof-grabado"><span>Sin grabado en EIS</span>${enlace}</div>${nota}`;
        }
        return `
            <div class="cof-grabado">
                ${etiqueta(g.estado, g.estado_display)}
                ${etiqueta('tipo-' + g.tipo, g.tipo_display)}
                <small>Ubicación: ${escapar(g.ubicacion)}</small>
                <button type="button" class="boton boton--detalle" data-grabado="${g.id}"
                        aria-label="Ver detalle del grabado ${escapar(g.of_origen)} ${escapar(p.proceso)}">Ver detalle</button>
            </div>${nota}`;
    }

    function renderizar(r) {
        $('cof-titulo-of').textContent = r.of;
        $('cof-cliente').textContent = r.datos.cliente || '—';
        $('cof-descripcion').textContent = r.datos.descripcion || '—';
        $('cof-sobre').textContent = r.datos.sobre || '—';
        $('cof-of-referencia').textContent = r.datos.of_referencia || '—';

        $('cof-procesos').innerHTML = r.procesos.map((p) => `
            <tr>
                <td data-label="Proceso"><strong>${escapar(p.proceso)}</strong></td>
                <td data-label="OF de referencia">${escapar(p.of_referencia)}</td>
                <td data-label="Acabado">${celdaAcabado(p)}</td>
                <td data-label="Grabado en EIS" class="cof-celda-grabado">${celdaGrabado(p)}</td>
            </tr>`).join('');
        $('cof-resultado').style.display = '';
    }

    function consultar(of) {
        ofActual = of;
        const boton = $('cof-btn');
        boton.disabled = true;
        ocultarAviso();
        fetch(`/grabados/api/consultor/?${new URLSearchParams({ of })}`)
            .then((resp) => resp.json().catch(() => ({ status: 'error', message: `Error del servidor (${resp.status}).` })))
            .then((r) => {
                if (r.status !== 'ok') {
                    $('cof-resultado').style.display = 'none';
                    mostrarAviso(r.message || 'No se pudo consultar la OF.', 'error');
                    return;
                }
                $('cof-of').value = r.of;
                if (!r.encontrado) {
                    mostrarAviso(`La API no encontró la OF ${r.of}. Revisa el número; abajo ves si existe algún grabado en EIS con esa OF.`, 'advertencia');
                }
                renderizar(r);
            })
            .catch(() => {
                $('cof-resultado').style.display = 'none';
                mostrarAviso('No se pudo conectar con el servidor. Revisa tu conexión e intenta de nuevo.', 'error');
            })
            .finally(() => { boton.disabled = false; });
    }

    $('cof-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const of = $('cof-of').value.trim();
        if (!of) {
            $('cof-resultado').style.display = 'none';
            mostrarAviso('Ingresa un número de OF.', 'error');
            $('cof-of').focus();
            return;
        }
        consultar(of);
    });

    $('cof-procesos').addEventListener('click', (e) => {
        const boton = e.target.closest('[data-grabado]');
        if (boton) PanelGrabado.abrir(Number(boton.dataset.grabado));
    });

    // Si se desactiva/reactiva desde el panel, se vuelve a consultar la misma OF.
    document.addEventListener('panelgrabado:cambio', () => { if (ofActual) consultar(ofActual); });

    // ?of=22741 en la dirección consulta directo.
    const ofInicial = new URLSearchParams(window.location.search).get('of');
    if (ofInicial) {
        $('cof-of').value = ofInicial;
        consultar(ofInicial.trim());
    } else {
        $('cof-of').focus();
    }
})();

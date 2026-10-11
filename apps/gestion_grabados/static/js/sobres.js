/* ============================================================
   Sobres — ver views_sobres.py (api_sobres)
   Búsqueda y filtros por columna (embudo, componente FiltroColumna) en el
   servidor (OF, código o ubicación; selectors.CAMPOS_SOBRES), hasta 500 filas.
   La ubicación se edita con doble clic en su celda (Enter guarda, Esc
   cancela; el servidor valida el formato, ej. GBB-2-1). Desactivar pide
   el motivo en un modal; Reactivar es directo.
   ============================================================ */

(function () {
    'use strict';

    const LARGO_MINIMO_MOTIVO = 5;
    const COLUMNAS = 7;

    let q = '';
    let sobres = [];
    let pedidoActual = 0;              // descarta respuestas viejas al escribir rápido
    let sobreADesactivar = null;

    const $ = (id) => document.getElementById(id);
    const cuerpo = $('sob-cuerpo');

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function escapar(valor) {
        const div = document.createElement('div');
        div.textContent = valor === null || valor === undefined || valor === '' ? '—' : String(valor);
        return div.innerHTML;
    }

    function debounce(fn, espera) {
        let temporizador;
        return (...args) => { clearTimeout(temporizador); temporizador = setTimeout(() => fn(...args), espera); };
    }

    // Texto largo en una línea con "..."; el texto completo en el tooltip (grabados_tabla.css).
    function celdaTexto(valor) {
        const texto = escapar(valor);
        return `<span class="celda-texto" title="${texto.replace(/"/g, '&quot;')}">${texto}</span>`;
    }

    function mensajeTabla(texto) {
        cuerpo.innerHTML = `<tr><td colspan="${COLUMNAS}" class="tabla-sin-resultados">${escapar(texto)}</td></tr>`;
    }

    function postJson(url, datos) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify(datos || {}),
        }).then(r => r.json()).then(res => {
            if (res.status !== 'ok') throw new Error(res.message || 'No se pudo guardar.');
            return res;
        });
    }

    function textoError(err) {
        return err.message === 'Failed to fetch' ? 'Error de conexión. Inténtalo de nuevo.' : err.message;
    }

    // ---------------------------------------------------------------- render
    function celdaUbicacion(s) {
        if (s.estado === 'INACTIVO') {
            return `<td data-label="Ubicación" class="sob-ubicacion-bloqueada"
                        title="Sobre inactivo: reactívalo para cambiar la ubicación">${escapar(s.ubicacion)}</td>`;
        }
        return `<td data-label="Ubicación" class="sob-ubicacion" tabindex="0"
                    title="Doble clic para editar la ubicación">${escapar(s.ubicacion)}</td>`;
    }

    function celdaEstado(s) {
        const titulo = s.estado === 'INACTIVO'
            ? ` title="${escapar(`Desactivado por ${s.desactivado_por || '—'} el ${s.desactivado_el || '—'}: ${s.motivo_desactivacion}`)}"`
            : '';
        return `<td data-label="Estado"><span class="pg-etiqueta pg-etiqueta--${escapar(s.estado)}"${titulo}>${escapar(s.estado_display)}</span></td>`;
    }

    function celdaAcciones(s) {
        const boton = s.estado === 'INACTIVO'
            ? `<button type="button" class="pg-boton pg-boton--reactivar" data-accion="reactivar">Reactivar</button>`
            : `<button type="button" class="pg-boton pg-boton--desactivar" data-accion="desactivar">Desactivar</button>`;
        return `<td data-label="Acciones" class="sob-acciones">${boton}</td>`;
    }

    function renderizar(res) {
        $('sob-contador').textContent = `Mostrando ${res.data.length} de ${res.total}`;
        if (!sobres.length) {
            mensajeTabla(q || filtroColumna.hay() ? 'Ningún sobre coincide con la búsqueda o los filtros.'
                                                  : 'Todavía no hay sobres registrados.');
            return;
        }
        cuerpo.innerHTML = sobres.map(s => `
            <tr class="sob-fila" data-id="${s.id}">
                <td data-label="OF de origen"><strong>${escapar(s.of_origen)}</strong></td>
                <td data-label="Proceso">${escapar(s.proceso)}</td>
                <td data-label="Código">${celdaTexto(s.codigo)}</td>
                ${celdaUbicacion(s)}
                ${celdaEstado(s)}
                <td data-label="Última modificación" class="sob-modificado">${escapar(s.actualizado_el)}</td>
                ${celdaAcciones(s)}
            </tr>`).join('');
    }

    // ----------------------------------------------------------------- datos
    function cargar() {
        const pedido = ++pedidoActual;
        const params = new URLSearchParams();
        if (q) params.set('q', q);
        const deColumna = filtroColumna.paraServidor();
        if (deColumna.length) params.set('filtros', JSON.stringify(deColumna));
        fetch(`/grabados/api/sobres/?${params}`)
            .then(r => r.json())
            .then(res => {
                if (pedido !== pedidoActual) return;
                if (res.status !== 'ok') { mensajeTabla(res.message || 'No se pudieron cargar los sobres.'); return; }
                sobres = res.data;
                renderizar(res);
            })
            .catch(() => { if (pedido === pedidoActual) mensajeTabla('Error de conexión al cargar los sobres.'); });
    }

    // ---------------------------------------------------- filtros por columna
    // Las opciones salen de todos los sobres (respetando la búsqueda).
    const filtroColumna = window.FiltroColumna.crear({
        ids: { panel: 'filtro-columna-panel', lista: 'filtro-columna-lista', titulo: 'filtro-columna-titulo',
               limpiar: 'sob-limpiar-columna', quitarTodos: 'sob-quitar-columnas' },
        idPunto: (col) => `sob-punto-${col}`,
        columnasFecha: ['modificado'],
        obtenerValores: (col) => {
            const params = new URLSearchParams({ col });
            if (q) params.set('q', q);
            return fetch(`/grabados/api/sobres/valores/?${params}`)
                .then(r => r.json())
                .then(res => {
                    if (res.status !== 'ok') throw new Error(res.message);
                    return res;
                });
        },
        alCambiar: () => cargar(),
    });

    function sobreDeFila(fila) {
        return sobres.find(s => s.id === Number(fila.dataset.id));
    }

    // ------------------------------------------------------ editar ubicación
    function editarUbicacion(celda) {
        if (celda.querySelector('input')) return;
        const fila = celda.closest('.sob-fila');
        const sobre = sobreDeFila(fila);
        // Un solo hijo: en celular la celda es flex (etiqueta a un lado, valor al otro).
        celda.innerHTML = `<div>
            <input type="text" class="sob-ubicacion__input" maxlength="20" autocomplete="off"
                   placeholder="Ej: GBB-2-1" aria-label="Nueva ubicación del sobre">
            <div class="sob-ubicacion__error" role="alert"></div></div>`;
        const input = celda.querySelector('input');
        input.value = sobre.ubicacion || '';
        input.focus();
        input.select();

        let guardando = false;
        const cerrar = () => { celda.innerHTML = escapar(sobre.ubicacion); };
        const mostrarError = (texto) => { celda.querySelector('.sob-ubicacion__error').textContent = texto; };

        input.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Escape') { e.preventDefault(); cerrar(); celda.focus(); }
            if (e.key !== 'Enter' || guardando) return;
            e.preventDefault();
            guardando = true;
            input.disabled = true;
            mostrarError('');
            postJson(`/grabados/api/sobre/${sobre.id}/ubicacion/`, { ubicacion: input.value })
                .then(res => {
                    sobre.ubicacion = res.ubicacion;
                    sobre.actualizado_el = res.actualizado_el;
                    fila.querySelector('.sob-modificado').innerHTML = escapar(res.actualizado_el);
                    cerrar();
                    celda.focus();
                })
                .catch(err => {
                    guardando = false;
                    input.disabled = false;
                    input.focus();
                    mostrarError(textoError(err));
                });
        });
        // Salir sin Enter cancela (mientras no se esté guardando).
        input.addEventListener('blur', () => { if (!guardando) cerrar(); });
    }

    // -------------------------------------------------- desactivar / reactivar
    function avisoModal(texto) {
        const aviso = $('sob-desactivar-aviso');
        aviso.textContent = texto || '';
        aviso.style.display = texto ? '' : 'none';
    }

    function abrirDesactivar(sobre) {
        sobreADesactivar = sobre;
        $('sob-desactivar-subtitulo').textContent =
            `Sobre ${sobre.codigo || '(sin código)'} · OF ${sobre.of_origen} ${sobre.proceso}`;
        $('sob-motivo').value = '';
        avisoModal('');
        $('sob-btn-confirmar').disabled = false;
        $('sob-modal-desactivar').style.display = 'flex';
        $('sob-motivo').focus();
    }

    function cerrarDesactivar() {
        $('sob-modal-desactivar').style.display = 'none';
        sobreADesactivar = null;
    }

    function confirmarDesactivar() {
        const motivo = $('sob-motivo').value.trim();
        if (motivo.length < LARGO_MINIMO_MOTIVO) {
            avisoModal(`Escribe el motivo (al menos ${LARGO_MINIMO_MOTIVO} caracteres).`);
            $('sob-motivo').focus();
            return;
        }
        const btn = $('sob-btn-confirmar');
        btn.disabled = true;
        postJson(`/grabados/api/sobre/${sobreADesactivar.id}/desactivar/`, { motivo })
            .then(() => { cerrarDesactivar(); cargar(); })
            .catch(err => { btn.disabled = false; avisoModal(textoError(err)); });
    }

    function reactivar(sobre, boton) {
        boton.disabled = true;
        postJson(`/grabados/api/sobre/${sobre.id}/reactivar/`)
            .then(cargar)
            .catch(err => { boton.disabled = false; alertaFila(boton, textoError(err)); });
    }

    function alertaFila(boton, texto) {
        // Sin alert(): el error queda debajo del botón.
        let error = boton.parentElement.querySelector('.sob-ubicacion__error');
        if (!error) {
            error = document.createElement('div');
            error.className = 'sob-ubicacion__error';
            error.setAttribute('role', 'alert');
            boton.parentElement.appendChild(error);
        }
        error.textContent = texto;
    }

    // --------------------------------------------------------------- eventos
    $('sob-buscador').addEventListener('input', debounce((e) => {
        q = e.target.value.trim();
        cargar();
    }, 300));

    cuerpo.addEventListener('click', (e) => {
        const boton = e.target.closest('[data-accion]');
        if (!boton) return;
        const sobre = sobreDeFila(boton.closest('.sob-fila'));
        if (boton.dataset.accion === 'desactivar') abrirDesactivar(sobre);
        else reactivar(sobre, boton);
    });

    cuerpo.addEventListener('dblclick', (e) => {
        const celda = e.target.closest('.sob-ubicacion');
        if (celda) editarUbicacion(celda);
    });

    cuerpo.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.classList.contains('sob-ubicacion')) {
            e.preventDefault();
            editarUbicacion(e.target);   // con teclado: Enter sobre la celda
        }
    });

    $('sob-btn-cancelar').addEventListener('click', cerrarDesactivar);
    $('sob-btn-confirmar').addEventListener('click', confirmarDesactivar);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && $('sob-modal-desactivar').style.display === 'flex') cerrarDesactivar();
    });

    cargar();
})();

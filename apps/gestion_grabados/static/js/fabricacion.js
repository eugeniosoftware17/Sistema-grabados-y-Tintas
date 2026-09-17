/* ============================================================
   Alta manual de "Fabricación" — ver views_fabricacion.py
   Módulo TEMPORAL: solo para cargar grabados que ya están en stock
   y no pasaron por Plani. Sin datos técnicos de producción.
   ============================================================ */

(function () {
    'use strict';

    let registrosSesion = [];
    let palabrasBusqueda = [];

    const ESTADOS = {
        PENDIENTE: 'Pendiente', EN_PROCESO: 'En Proceso', EN_MAQUINA: 'En Máquina',
        COMPLETADO: 'Completado', REVISION: 'En Revisión', REPETIR: 'Para Repetir', CANCELADO: 'Cancelado',
    };

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function debounce(fn, espera) {
        let temporizador;
        return function (...args) {
            clearTimeout(temporizador);
            temporizador = setTimeout(() => fn.apply(this, args), espera);
        };
    }

    function limpiarFormulario() {
        ['fab-of', 'fab-maquina', 'fab-sobre', 'fab-referencia', 'fab-papel', 'fab-cliente', 'fab-ubicacion', 'fab-descripcion']
            .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
        document.getElementById('fab-proceso').value = 'STAMPING';
        document.getElementById('fab-estado').value = 'COMPLETADO';
        ocultarAvisos();
    }

    function ocultarAvisos() {
        document.getElementById('fab-aviso-ok').style.display = 'none';
        document.getElementById('fab-aviso-vacio').style.display = 'none';
        document.getElementById('fab-aviso-doble-proceso').style.display = 'none';
        document.getElementById('fab-proceso').classList.remove('control-entrada--advertencia');
    }

    window.abrirModalFabricacion = function () {
        limpiarFormulario();
        document.getElementById('modal-fabricacion').style.display = 'flex';
        document.getElementById('fab-of').focus();
    };

    window.cerrarModalFabricacion = function () {
        document.getElementById('modal-fabricacion').style.display = 'none';
    };

    window.buscarExterno = function (reintento) {
        const of = document.getElementById('fab-of').value.trim();
        const proceso = document.getElementById('fab-proceso').value;
        if (!of) { alert('Ingresá un número de OF primero.'); return; }

        ocultarAvisos();

        fetch(`/grabados/api/fabricacion/buscar-externo/?of=${encodeURIComponent(of)}&proceso=${encodeURIComponent(proceso)}`)
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') { alert('Error: ' + res.message); return; }
                const d = res.data;
                if (d.encontrado_ext) {
                    // Si el sistema externo indica que la orden es del otro proceso,
                    // se ajusta el select y se repite la búsqueda una sola vez para
                    // traer ref_ext/acabado_ext (y el status_db) del proceso correcto.
                    const procesoDetectado = (d.proceso_ext === 'STAMPING' || d.proceso_ext === 'EMBOSSING') ? d.proceso_ext : null;
                    if (procesoDetectado && procesoDetectado !== proceso && !reintento) {
                        document.getElementById('fab-proceso').value = procesoDetectado;
                        window.buscarExterno(true);
                        return;
                    }
                    if (procesoDetectado) {
                        document.getElementById('fab-proceso').value = procesoDetectado;
                    }
                }

                if (d.status_db === 'existente') {
                    const continuar = confirm('Esta OF ya está registrada en el sistema. ¿Deseas continuar de todas formas?');
                    if (!continuar) {
                        limpiarFormulario();
                        return;
                    }
                }

                if (d.encontrado_ext) {
                    document.getElementById('fab-sobre').value = d.sobre_ext !== '—' ? d.sobre_ext : '';
                    document.getElementById('fab-referencia').value = d.ref_ext !== '—' ? d.ref_ext : '';
                    if (d.cliente_ext && d.cliente_ext !== '—') {
                        document.getElementById('fab-cliente').value = d.cliente_ext;
                    }
                    if (d.descripcion_ext && d.descripcion_ext !== '—') {
                        document.getElementById('fab-descripcion').value = d.descripcion_ext;
                    }

                    // Si la orden tiene datos para los dos procesos, no se puede saber
                    // cuál corresponde: se avisa para que el usuario elija a mano.
                    const tieneStamping = d.of_stamping_ext && d.of_stamping_ext !== '—';
                    const tieneEmbossing = d.of_embossing_ext && d.of_embossing_ext !== '—';
                    if (tieneStamping && tieneEmbossing) {
                        document.getElementById('fab-aviso-doble-proceso').style.display = 'block';
                        document.getElementById('fab-proceso').classList.add('control-entrada--advertencia');
                    }

                    document.getElementById('fab-aviso-ok').style.display = 'block';
                } else {
                    document.getElementById('fab-aviso-vacio').style.display = 'block';
                }
            })
            .catch(() => alert('Error de conexión al buscar en el sistema externo.'));
    };

    function coincideBusqueda(r) {
        if (!palabrasBusqueda.length) return true;
        const pool = [r.of, r.cliente, r.descripcion, r.proceso, r.ubicacion, ESTADOS[r.estado] || r.estado]
            .join(' ').toLowerCase();
        return palabrasBusqueda.every(p => pool.includes(p));
    }

    function renderTabla() {
        const tbody = document.getElementById('tabla-cuerpo-fabricacion');
        if (!registrosSesion.length) {
            tbody.innerHTML = '<tr><td colspan="11" class="tabla-sin-resultados">Todavía no registraste nada en esta sesión.</td></tr>';
            return;
        }
        const filas = registrosSesion
            .map((r, idx) => ({ r, idx }))
            .filter(({ r }) => coincideBusqueda(r));

        if (!filas.length) {
            tbody.innerHTML = '<tr><td colspan="11" class="tabla-sin-resultados">Ningún registro coincide con la búsqueda.</td></tr>';
            return;
        }

        tbody.innerHTML = filas.map(({ r, idx }) => {
            let htmlAcciones = `<div class="fila-acciones">
                <button class="boton-accion boton-accion--editar-azul" onclick="window.editarRegistroFabricacion(${idx})" title="Editar">
                    <svg viewBox="0 0 24 24" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>`;
            if (window.USER_IS_ADMIN === true) {
                htmlAcciones += `
                <button class="boton-accion boton-accion--eliminar" onclick="window.eliminarRegistroFabricacion(${idx})" title="Eliminar">
                    <svg viewBox="0 0 24 24" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
                </button>`;
            }
            htmlAcciones += `</div>`;

            return `
            <tr>
                <td><strong>${r.of}</strong></td>
                <td>${r.proceso}</td>
                <td>${r.cliente || '—'}</td>
                <td>${r.descripcion || '—'}</td>
                <td>${r.maquina || '—'}</td>
                <td>${r.sobre || '—'}</td>
                <td>${r.referencia || '—'}</td>
                <td>${r.papel || '—'}</td>
                <td>${r.ubicacion || '—'}</td>
                <td>${ESTADOS[r.estado] || r.estado}</td>
                <td>${htmlAcciones}</td>
            </tr>
        `;
        }).join('');
    }

    window.editarRegistroFabricacion = function (idx) {
        const reg = registrosSesion[idx];
        if (!reg) return;
        ocultarAvisos();
        document.getElementById('fab-of').value = reg.of || '';
        document.getElementById('fab-proceso').value = reg.proceso || 'STAMPING';
        document.getElementById('fab-maquina').value = reg.maquina || '';
        document.getElementById('fab-sobre').value = reg.sobre || '';
        document.getElementById('fab-referencia').value = reg.referencia || '';
        document.getElementById('fab-papel').value = reg.papel || '';
        document.getElementById('fab-cliente').value = reg.cliente || '';
        document.getElementById('fab-ubicacion').value = reg.ubicacion || '';
        document.getElementById('fab-descripcion').value = (reg.descripcion && reg.descripcion !== '—') ? reg.descripcion : '';
        document.getElementById('fab-estado').value = reg.estado || 'COMPLETADO';
        document.getElementById('modal-fabricacion').style.display = 'flex';
    };

    window.eliminarRegistroFabricacion = function (idx) {
        const reg = registrosSesion[idx];
        if (!reg) return;
        if (!confirm(`¿Estás seguro que deseas eliminar la OF ${reg.of}?`)) return;

        fetch('/grabados/api/eliminar/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify({ of: reg.of, proceso: reg.proceso }),
        })
            .then(r => r.json())
            .then(res => {
                if (res.status === 'ok') {
                    registrosSesion.splice(idx, 1);
                    renderTabla();
                } else {
                    alert('Error: ' + res.message);
                }
            })
            .catch(() => alert('Error de conexión al eliminar.'));
    };

    window.guardarFabricacion = function () {
        const of = document.getElementById('fab-of').value.trim();
        const proceso = document.getElementById('fab-proceso').value;
        if (!of) { alert('El número de OF es obligatorio.'); return; }

        const payload = {
            of: of,
            proceso: proceso,
            maquina: document.getElementById('fab-maquina').value.trim(),
            sobre: document.getElementById('fab-sobre').value.trim(),
            referencia: document.getElementById('fab-referencia').value.trim(),
            papel: document.getElementById('fab-papel').value.trim(),
            cliente: document.getElementById('fab-cliente').value.trim(),
            ubicacion: document.getElementById('fab-ubicacion').value.trim(),
            descripcion: document.getElementById('fab-descripcion').value.trim(),
            estado: document.getElementById('fab-estado').value,
        };

        const btn = document.getElementById('fab-btn-guardar');
        btn.disabled = true;

        fetch('/grabados/api/fabricacion/registrar/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify(payload),
        })
            .then(r => r.json())
            .then(res => {
                if (res.status === 'ok') {
                    // Si ya existía en la tabla de esta sesión (edición), se reemplaza
                    // en el mismo lugar en vez de agregar una fila duplicada.
                    const idxExistente = registrosSesion.findIndex(r => r.of === payload.of && r.proceso === payload.proceso);
                    if (idxExistente >= 0) {
                        registrosSesion[idxExistente] = payload;
                    } else {
                        registrosSesion.unshift(payload);
                    }
                    renderTabla();
                    cerrarModalFabricacion();
                } else {
                    alert('Error: ' + res.message);
                }
            })
            .catch(() => alert('Error de conexión al guardar.'))
            .finally(() => { btn.disabled = false; });
    };

    function limpiarFormularioLote() {
        document.getElementById('lote-ofs').value = '';
        document.getElementById('lote-proceso').value = 'STAMPING';
        document.getElementById('lote-estado').value = 'COMPLETADO';
        document.getElementById('lote-ubicacion').value = '';
        const resumenTexto = document.getElementById('lote-resumen-texto');
        resumenTexto.style.display = 'none';
        resumenTexto.textContent = '';
        document.getElementById('lote-resumen-tabla-contenedor').style.display = 'none';
        document.getElementById('lote-resumen-tabla-cuerpo').innerHTML = '';
    }

    window.abrirModalLote = function () {
        limpiarFormularioLote();
        document.getElementById('modal-fabricacion-lote').style.display = 'flex';
        document.getElementById('lote-ofs').focus();
    };

    window.cerrarModalLote = function () {
        document.getElementById('modal-fabricacion-lote').style.display = 'none';
    };

    window.guardarLote = function () {
        const ofs = document.getElementById('lote-ofs').value.trim();
        if (!ofs) { alert('Ingresá al menos una OF.'); return; }

        const payload = {
            ofs: ofs,
            proceso: document.getElementById('lote-proceso').value,
            estado: document.getElementById('lote-estado').value,
            ubicacion: document.getElementById('lote-ubicacion').value.trim(),
        };

        const btn = document.getElementById('lote-btn-guardar');
        btn.disabled = true;

        fetch('/grabados/api/fabricacion/registrar-lote/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify(payload),
        })
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') { alert('Error: ' + res.message); return; }

                const resumenTexto = document.getElementById('lote-resumen-texto');
                resumenTexto.textContent = `${res.registrados} registrada(s), ${res.omitidos} omitida(s).`;
                resumenTexto.className = 'aviso-externo ' + (res.omitidos ? 'aviso-externo--advertencia' : 'aviso-externo--ok');
                resumenTexto.style.display = 'block';

                renderResumenLote(res.detalle, payload.proceso);

                cargarRegistrados();
            })
            .catch(() => alert('Error de conexión al guardar en lote.'))
            .finally(() => { btn.disabled = false; });
    };

    function renderResumenLote(detalle, proceso) {
        const contenedor = document.getElementById('lote-resumen-tabla-contenedor');
        const cuerpo = document.getElementById('lote-resumen-tabla-cuerpo');

        cuerpo.innerHTML = detalle.map(d => {
            const esOmitida = d.status === 'omitido';
            const badge = esOmitida
                ? '<span class="badge-lote badge-lote--omitida">Omitida - ya existe</span>'
                : '<span class="badge-lote badge-lote--registrada">Registrada</span>';
            const ubicacionTexto = d.ubicacion || '—';
            const celdaUbicacion = esOmitida
                ? `<td class="celda-ubicacion celda-ubicacion--editable" data-of="${d.of}" data-proceso="${proceso}" data-ubicacion="${d.ubicacion || ''}" title="Doble clic para editar">${ubicacionTexto}</td>`
                : `<td>${ubicacionTexto}</td>`;
            return `<tr><td><strong>${d.of}</strong></td><td>${badge}</td>${celdaUbicacion}</tr>`;
        }).join('');

        cuerpo.querySelectorAll('.celda-ubicacion--editable').forEach(celda => {
            celda.addEventListener('dblclick', () => activarEdicionUbicacion(celda));
        });

        contenedor.style.display = 'block';
    }

    function activarEdicionUbicacion(celda) {
        if (celda.querySelector('input')) return; // ya está en edición

        const valorActual = celda.dataset.ubicacion || '';
        celda.innerHTML = `<input type="text" value="${valorActual.replace(/"/g, '&quot;')}">`;
        const input = celda.querySelector('input');
        input.focus();
        input.select();

        let guardado = false;
        const confirmar = () => {
            if (guardado) return;
            guardado = true;
            guardarUbicacionCelda(celda, input.value.trim());
        };

        input.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
            else if (ev.key === 'Escape') { guardado = true; renderCeldaUbicacion(celda, valorActual); }
        });
        input.addEventListener('blur', confirmar);
    }

    function renderCeldaUbicacion(celda, ubicacion) {
        celda.dataset.ubicacion = ubicacion || '';
        celda.textContent = ubicacion || '—';
    }

    function guardarUbicacionCelda(celda, nuevaUbicacion) {
        const of = celda.dataset.of;
        const proceso = celda.dataset.proceso;

        celda.innerHTML = '<em style="color:#999;">Guardando…</em>';

        fetch('/grabados/api/fabricacion/editar-ubicacion/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify({ of: of, proceso: proceso, ubicacion: nuevaUbicacion }),
        })
            .then(r => r.json())
            .then(res => {
                if (res.status === 'ok') {
                    renderCeldaUbicacion(celda, res.ubicacion);
                    cargarRegistrados();
                } else {
                    alert('Error: ' + res.message);
                    renderCeldaUbicacion(celda, celda.dataset.ubicacion);
                }
            })
            .catch(() => {
                alert('Error de conexión al guardar la ubicación.');
                renderCeldaUbicacion(celda, celda.dataset.ubicacion);
            });
    }

    function cargarRegistrados() {
        fetch('/grabados/api/fabricacion/listar/')
            .then(r => r.json())
            .then(res => {
                if (res.status === 'ok') {
                    registrosSesion = res.data;
                    renderTabla();
                }
            })
            .catch(() => renderTabla());
    }

    const buscador = document.getElementById('fab-buscador-input');
    if (buscador) {
        buscador.addEventListener('input', debounce(e => {
            palabrasBusqueda = e.target.value.toLowerCase().trim().split(/\s+/).filter(p => p);
            renderTabla();
        }, 300));
    }

    cargarRegistrados();
})();

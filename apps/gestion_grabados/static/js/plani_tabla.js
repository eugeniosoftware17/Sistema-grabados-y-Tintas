/* ============================================================
   PLANI (fase 3): mandar el grabado a máquina y recogerlo.
   - La acción de cada fila la decide el servidor (fila.grabado.accion,
     ver selectors.estado_grabados_para_plani); aquí solo se pinta.
   - Ya no se cargan datos técnicos: eso es Alta de Grabado.
   - El detalle del grabado se abre con el componente PanelGrabado.
   ============================================================ */

(function () {
    'use strict';

    let datosPlani = [];
    let filtrados = [];
    let filaActiva = null;            // fila del modal abierto
    let grabadoElegido = null;        // {id, of_origen} para mandar a máquina
    let pedidoOtros = 0;

    const $ = (id) => document.getElementById(id);
    const MAQUINAS_ACTIVAS = new Set(JSON.parse($('plani-maquinas-activas').textContent || '[]'));
    const LARGO_MINIMO_COMENTARIO = 5;

    // ---------------------------------------------------------- utilidades
    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function escapar(valor) {
        const div = document.createElement('div');
        div.textContent = valor === null || valor === undefined || valor === '' ? '—' : String(valor);
        return div.innerHTML;
    }

    function sinDato(valor) {
        return valor === null || valor === undefined || String(valor).trim() === '' || valor === '—';
    }

    function numero(valor, decimales) {
        if (sinDato(valor) || !Number.isFinite(Number(valor))) return valor;
        return Number(valor).toLocaleString('es-DO', { maximumFractionDigits: decimales });
    }

    function normalizarMaquina(texto) {
        return String(texto || '').split(/\s+/).filter(Boolean).join(' ').toUpperCase();
    }

    function etiqueta(codigo, texto) {
        return `<span class="pg-etiqueta pg-etiqueta--${escapar(codigo)}">${escapar(texto)}</span>`;
    }

    function debounce(fn, espera) {
        let t;
        return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), espera); };
    }

    function mostrarAviso(id, clase, texto) {
        const el = $(id);
        el.className = 'aviso ' + clase;
        el.textContent = texto;
        el.style.display = 'block';
    }

    function abrirModal(id) { $(id).style.display = 'flex'; }
    function cerrarModal(id) { $(id).style.display = 'none'; filaActiva = null; }

    function postJson(url, datos) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify(datos),
        }).then(r => r.json());
    }

    // ------------------------------------------------------- sincronización
    function sincronizar(silencioso) {
        const btn = $('btn-sincronizar');
        const overlay = $('overlay-proceso');
        if (!silencioso) {
            $('overlay-titulo').textContent = 'Consultando datos...';
            $('overlay-mensaje').textContent = 'Leyendo el Excel de planificación y el estado de los grabados.';
            $('overlay-spinner').style.display = 'block';
            overlay.style.display = 'flex';
        }
        btn.disabled = true;

        return fetch('/grabados/api/sincronizar/')
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') {
                    overlay.style.display = 'none';
                    alert('Error: ' + res.message);
                    return;
                }
                datosPlani = res.data;
                aplicarBusqueda();
                $('btn-exportar').disabled = !datosPlani.length;
                if (!silencioso) mostrarResumenSincronizacion(res.stats);
            })
            .catch(() => {
                overlay.style.display = 'none';
                alert('Error de conexión con el servidor.');
            })
            .finally(() => { btn.disabled = false; });
    }

    function mostrarResumenSincronizacion(s) {
        $('overlay-spinner').style.display = 'none';
        $('overlay-titulo').textContent = '¡Sincronización completada!';
        const errores = (s.errores_detalle || []).slice(0, 5).map(e => `<li>${escapar(e)}</li>`).join('');
        $('overlay-mensaje').innerHTML = `
            <div style="text-align:left; margin-top:15px; background:#f9f9f9; padding:15px; border-radius:10px; border:1px solid #ddd; max-height:250px; overflow-y:auto;">
                <p>📊 <strong>Filas en el Excel:</strong> ${escapar(s.total_filas_excel)}</p>
                <p style="color:#2d8a3e;">✅ <strong>Cargadas:</strong> ${escapar(s.procesados_ok)}</p>
                <p style="color:#f39c12;">📑 <strong>Duplicadas omitidas:</strong> ${escapar(s.duplicados_omitidos || 0)}</p>
                <p style="color:${s.con_error > 0 ? '#d32f2f' : '#666'};">❌ <strong>Con errores o vacías:</strong> ${escapar(s.con_error)}</p>
                ${errores ? `<ul style="margin-top:10px; font-size:11px; color:#d32f2f;">${errores}</ul>` : ''}
            </div>
            <button type="button" class="boton boton--primario" id="overlay-continuar"
                    style="margin-top:20px; width:100%; background-color:#2d8a3e;">Continuar</button>`;
        $('overlay-continuar').addEventListener('click', () => { $('overlay-proceso').style.display = 'none'; });
        $('overlay-continuar').focus();
    }

    // ---------------------------------------------------------------- render
    function textoGrabadoDeOtra(g) {
        return g && g.usa_grabado_de_otra ? `<span class="plani-nota">Grabado de la OF ${escapar(g.of_origen)}</span>` : '';
    }

    function botonVer(g) {
        return g ? `<button type="button" class="plani-enlace" data-accion="ver">Ver grabado</button>` : '';
    }

    function celdaGestion(fila) {
        const e = fila.grabado || {};
        const g = e.grabado;
        switch (e.accion) {
            case 'SIN_PROCESO':
                return `${etiqueta('neutra', 'Sin datos del proceso')}<span class="plani-nota">${escapar(e.mensaje)}</span>`;
            case 'DAR_DE_ALTA':
                return `<span class="plani-nota">${escapar(e.mensaje)}</span>
                    <div class="plani-gestion__botones">
                        <button type="button" class="boton boton--primario boton--chico" data-accion="alta">Dar de alta</button>
                    </div>`;
            case 'SIN_ACCION':
                return `${etiqueta(g.estado, g.estado_display)}${textoGrabadoDeOtra(g)}${botonVer(g)}`;
            case 'MANDAR':
                return `${etiqueta(g.estado, g.estado_display)}
                    ${g.ubicacion ? `<span class="plani-nota">📍 ${escapar(g.ubicacion)}</span>` : ''}${textoGrabadoDeOtra(g)}
                    <div class="plani-gestion__botones">
                        <button type="button" class="boton boton--primario boton--chico" data-accion="mandar">Mandar a máquina</button>
                    </div>${botonVer(g)}`;
            case 'MANDAR_OTRA_VEZ':
                return `<span class="plani-nota plani-nota--ok">✓ Completada el ${escapar(e.completada.fecha)}${
                        e.completada.ubicacion ? ' · ' + escapar(e.completada.ubicacion) : ''}</span>${textoGrabadoDeOtra(g)}
                    <div class="plani-gestion__botones">
                        <button type="button" class="boton boton--secundario boton--chico" data-accion="mandar-otra-vez">Mandar otra vez</button>
                    </div>${botonVer(g)}`;
            case 'RECOGER':
                return `${etiqueta('EN_MAQUINA', 'En máquina')}
                    ${e.terminada_en_planta ? '<span class="plani-terminada">Terminada en planta</span>' : ''}
                    <span class="plani-nota">${escapar(e.mensaje)}</span>${textoGrabadoDeOtra(g)}
                    <div class="plani-gestion__botones">
                        <button type="button" class="boton boton--chico" style="background:#1565c0; color:#fff;" data-accion="recoger">Recoger</button>
                    </div>${botonVer(g)}`;
            case 'EN_MAQUINA_OTRA':
                return `${etiqueta('EN_MAQUINA', e.mensaje)}${textoGrabadoDeOtra(g)}${botonVer(g)}`;
            case 'REFABRICAR':
                return `${etiqueta(g.estado, g.estado_display)}${textoGrabadoDeOtra(g)}
                    <div class="plani-gestion__botones">
                        <button type="button" class="boton boton--primario boton--chico" data-accion="refabricar">Refabricar</button>
                    </div>${botonVer(g)}`;
            default:
                return '—';
        }
    }

    function renderizarTabla() {
        const cuerpo = $('tabla-cuerpo');
        $('plani-contador').textContent = datosPlani.length ? `${filtrados.length} de ${datosPlani.length} fila(s)` : '';
        $('pie-info').textContent = datosPlani.length ? `Total en el Excel: ${datosPlani.length} fila(s)` : '';

        if (!datosPlani.length) {
            cuerpo.innerHTML = '<tr><td colspan="8" class="tabla-sin-resultados">Presiona "Actualizar desde Excel" para cargar la programación.</td></tr>';
            return;
        }
        if (!filtrados.length) {
            cuerpo.innerHTML = '<tr><td colspan="8" class="tabla-sin-resultados">Ninguna fila coincide con la búsqueda.</td></tr>';
            return;
        }
        cuerpo.innerHTML = filtrados.map(fila => {
            const indice = datosPlani.indexOf(fila);
            const terminada = fila.grabado && fila.grabado.terminada_en_planta;
            return `
            <tr class="${terminada ? 'plani-fila--terminada' : ''}" data-indice="${indice}">
                <td data-label="OF"><strong>${escapar(fila.of)}</strong></td>
                <td data-label="OF Ref.">${escapar(sinDato(fila.ref_ext) ? '' : fila.ref_ext)}</td>
                <td data-label="Descripción" class="plani-descripcion">${escapar(fila.descripcion)}</td>
                <td data-label="Cliente">${escapar(fila.cliente)}</td>
                <td data-label="Proceso">${escapar(fila.proceso)}</td>
                <td data-label="Máquina">${escapar(fila.maquina)}</td>
                <td data-label="Fecha prog.">${escapar(fila.fecha_programada)}</td>
                <td data-label="Grabado" class="plani-celda-gestion"><div class="plani-gestion">${celdaGestion(fila)}</div></td>
            </tr>`;
        }).join('');
    }

    function aplicarBusqueda() {
        const palabras = $('buscador-input').value.toLowerCase().split(/\s+/).filter(Boolean);
        filtrados = datosPlani.filter(f => {
            const texto = [f.of, f.ref_ext, f.cliente, f.descripcion].map(v => String(v || '').toLowerCase()).join(' ');
            return palabras.every(p => texto.includes(p));
        });
        renderizarTabla();
    }

    // -------------------------------------------------------------- exportar
    function exportar() {
        if (!window.XLSX || !filtrados.length) return;
        const filas = filtrados.map(f => {
            const e = f.grabado || {};
            return {
                'OF': f.of, 'OF Ref.': sinDato(f.ref_ext) ? '' : f.ref_ext, 'Descripción': f.descripcion || '',
                'Cliente': f.cliente || '', 'Proceso': f.proceso, 'Máquina': f.maquina || '',
                'Fecha prog.': f.fecha_programada || '', 'Grabado': e.grabado ? e.grabado.of_origen : '',
                'Estado del grabado': e.grabado ? e.grabado.estado_display : '', 'Situación': e.mensaje || '',
            };
        });
        const libro = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(libro, XLSX.utils.json_to_sheet(filas), 'Planning');
        XLSX.writeFile(libro, `planning_${new Date().toISOString().slice(0, 10)}.xlsx`);
    }

    // ------------------------------------------------------- mandar a máquina
    function abrirMandar(fila, otraVez) {
        filaActiva = fila;
        const e = fila.grabado;
        grabadoElegido = { id: e.grabado.id, of_origen: e.grabado.of_origen };

        $('mandar-subtitulo').textContent = `OF ${fila.of} · ${fila.proceso}`;
        const avisoOtraVez = $('mandar-aviso-otra-vez');
        if (otraVez) {
            avisoOtraVez.textContent = `Esta OF ya se completó el ${e.completada.fecha}. ¿Seguro que quieres mandarla otra vez a máquina?`;
            avisoOtraVez.style.display = 'block';
            $('mandar-btn-confirmar').textContent = 'Sí, mandar otra vez';
        } else {
            avisoOtraVez.style.display = 'none';
            $('mandar-btn-confirmar').textContent = 'Mandar a máquina';
        }
        pintarGrabadoElegido(e.grabado.of_origen === String(fila.of) ? 'Grabado de esta misma OF.'
            : `La OF usa el grabado de la OF ${e.grabado.of_origen}.`);

        $('mandar-resumen').innerHTML = [
            ['Máquina', fila.maquina], ['Fecha programada', fila.fecha_programada],
            ['Cantidad de formatos', numero(fila.cantidad_formatos, 0)], ['Horas de proceso', numero(fila.horas_proceso, 1)],
            ['Papel', fila.papel],
        ].map(([t, v]) => `<div><dt>${escapar(t)}</dt><dd>${escapar(v)}</dd></div>`).join('');

        $('mandar-otros').style.display = 'none';
        $('mandar-otros-buscar').value = '';
        $('mandar-otros-lista').innerHTML = '';
        $('mandar-btn-otro').style.display = '';

        // Aviso anticipado si la máquina del Excel no está en el catálogo (el servidor lo valida igual).
        const maquina = normalizarMaquina(fila.maquina);
        const btn = $('mandar-btn-confirmar');
        if (!maquina) {
            mostrarAviso('mandar-aviso', 'aviso--error', 'La fila del Excel no tiene máquina asignada: no se puede mandar a máquina.');
            btn.disabled = true;
        } else if (!MAQUINAS_ACTIVAS.has(maquina)) {
            mostrarAviso('mandar-aviso', 'aviso--error',
                `La máquina "${maquina}" no está en el catálogo; regístrala en el administrador (Gestión de grabados > Máquinas) y vuelve a intentarlo.`);
            btn.disabled = true;
        } else {
            $('mandar-aviso').style.display = 'none';
            btn.disabled = false;
        }
        abrirModal('modal-mandar');
        $('mandar-btn-cancelar').focus();
    }

    function pintarGrabadoElegido(nota) {
        $('mandar-grabado-texto').textContent = `Grabado ${grabadoElegido.of_origen} · ${filaActiva.proceso}`;
        $('mandar-grabado-nota').textContent = nota || '';
    }

    function buscarOtrosGrabados() {
        const pedido = ++pedidoOtros;
        const params = new URLSearchParams({ estado: 'APROBADO', proceso: filaActiva.proceso });
        const q = $('mandar-otros-buscar').value.trim();
        if (q) params.set('q', q);
        const lista = $('mandar-otros-lista');
        lista.innerHTML = '<li class="plani-nota">Buscando...</li>';
        fetch(`/grabados/api/inventario/?${params}`)
            .then(r => r.json())
            .then(res => {
                if (pedido !== pedidoOtros) return;
                if (res.status !== 'ok') { lista.innerHTML = `<li class="plani-nota">${escapar(res.message)}</li>`; return; }
                const grabados = res.data.slice(0, 50);
                if (!grabados.length) { lista.innerHTML = '<li class="plani-nota">No hay grabados aprobados que coincidan.</li>'; return; }
                lista.innerHTML = grabados.map(g => `
                    <li>
                        <input type="radio" name="mandar-otro" id="mandar-otro-${g.id}" value="${g.id}"
                               data-of="${escapar(g.of_origen)}" ${g.id === grabadoElegido.id ? 'checked' : ''}>
                        <label for="mandar-otro-${g.id}"><strong>${escapar(g.of_origen)}</strong> · ${escapar(g.cliente)}<br>
                            <span class="plani-nota">${escapar(g.referencia)}${g.ubicacion ? ' · 📍 ' + escapar(g.ubicacion) : ''}</span></label>
                    </li>`).join('');
            })
            .catch(() => { if (pedido === pedidoOtros) lista.innerHTML = '<li class="plani-nota">Error de conexión.</li>'; });
    }

    function confirmarMandar() {
        if (!filaActiva) return;
        const fila = filaActiva;
        const btn = $('mandar-btn-confirmar');
        btn.disabled = true;
        postJson('/grabados/api/plani/mandar-maquina/', {
            of: fila.of,
            proceso: fila.proceso,
            grabado_id: grabadoElegido.id,
            fila: {
                maquina: fila.maquina, fecha_programada: fila.fecha_programada,
                cantidad_formatos: fila.cantidad_formatos, horas_proceso: fila.horas_proceso, papel: fila.papel,
            },
        })
            .then(res => {
                if (res.status !== 'ok') { mostrarAviso('mandar-aviso', 'aviso--error', res.message); return; }
                cerrarModal('modal-mandar');
                alert(res.message);
                sincronizar(true);
            })
            .catch(() => mostrarAviso('mandar-aviso', 'aviso--error', 'Error de conexión al mandar a máquina.'))
            .finally(() => { btn.disabled = false; });
    }

    // ---------------------------------------------------------------- recoger
    function abrirRecoger(fila) {
        filaActiva = fila;
        const e = fila.grabado;
        $('recoger-subtitulo').textContent =
            `OF ${fila.of} · grabado ${e.grabado.of_origen} · ${e.envio.maquina}`;
        $('recoger-aviso-terminada').style.display = e.terminada_en_planta ? 'block' : 'none';
        $('recoger-form').reset();
        $('recoger-ubicacion').value = e.grabado.ubicacion || '';
        $('recoger-aviso').style.display = 'none';
        actualizarComentarioObligatorio();
        abrirModal('modal-recoger');
        $('recoger-ubicacion').focus();
    }

    function estadoFisico() {
        const marcado = document.querySelector('input[name="estado_fisico"]:checked');
        return marcado ? marcado.value : '';
    }

    function actualizarComentarioObligatorio() {
        const repetir = estadoFisico() === 'REPETIR';
        $('recoger-comentario-label').innerHTML = repetir
            ? `Motivo para repetir (obligatorio, al menos ${LARGO_MINIMO_COMENTARIO} caracteres): <span class="obligatorio">*</span>`
            : 'Comentario (opcional):';
    }

    function confirmarRecoger() {
        if (!filaActiva) return;
        const fila = filaActiva;
        const ubicacion = $('recoger-ubicacion').value.trim();
        const comentario = $('recoger-comentario').value.trim();
        if (!ubicacion) { mostrarAviso('recoger-aviso', 'aviso--error', 'Ingresa la ubicación física donde queda el grabado.'); return; }
        if (estadoFisico() === 'REPETIR' && comentario.length < LARGO_MINIMO_COMENTARIO) {
            mostrarAviso('recoger-aviso', 'aviso--error',
                `Para mandar el grabado a REPETIR explica el motivo (al menos ${LARGO_MINIMO_COMENTARIO} caracteres).`);
            return;
        }

        const datos = new FormData();   // form-urlencoded; ya no hay archivos
        datos.append('envio_id', fila.grabado.envio.id);
        datos.append('of', fila.of);
        datos.append('estado_fisico', estadoFisico());
        datos.append('ubicacion', ubicacion);
        datos.append('comentario', comentario);

        const btn = $('recoger-btn-confirmar');
        btn.disabled = true;
        fetch('/grabados/api/plani/recoger/', {
            method: 'POST', headers: { 'X-CSRFToken': getCookie('csrftoken') }, body: datos,
        })
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') { mostrarAviso('recoger-aviso', 'aviso--error', res.message); return; }
                cerrarModal('modal-recoger');
                alert(res.message);
                sincronizar(true);
            })
            .catch(() => mostrarAviso('recoger-aviso', 'aviso--error', 'Error de conexión al guardar la recogida.'))
            .finally(() => { btn.disabled = false; });
    }

    // --------------------------------------------------------------- eventos
    $('btn-sincronizar').addEventListener('click', () => sincronizar(false));
    $('btn-exportar').addEventListener('click', exportar);
    $('buscador-input').addEventListener('input', debounce(aplicarBusqueda, 200));

    $('tabla-cuerpo').addEventListener('click', (ev) => {
        const boton = ev.target.closest('[data-accion]');
        const filaHtml = ev.target.closest('tr[data-indice]');
        if (!boton || !filaHtml) return;
        const fila = datosPlani[Number(filaHtml.dataset.indice)];
        const e = fila.grabado || {};
        switch (boton.dataset.accion) {
            case 'ver': window.PanelGrabado.abrir(e.grabado.id); break;
            case 'alta':
            case 'refabricar':
                window.location.href = `/grabados/alta/?${new URLSearchParams(e.alta)}`;
                break;
            case 'mandar': abrirMandar(fila, false); break;
            case 'mandar-otra-vez': abrirMandar(fila, true); break;
            case 'recoger': abrirRecoger(fila); break;
        }
    });

    $('mandar-btn-cancelar').addEventListener('click', () => cerrarModal('modal-mandar'));
    $('mandar-btn-confirmar').addEventListener('click', confirmarMandar);
    $('mandar-btn-otro').addEventListener('click', () => {
        $('mandar-otros').style.display = 'block';
        $('mandar-btn-otro').style.display = 'none';
        buscarOtrosGrabados();
        $('mandar-otros-buscar').focus();
    });
    $('mandar-otros-buscar').addEventListener('input', debounce(buscarOtrosGrabados, 300));
    $('mandar-otros-lista').addEventListener('change', (ev) => {
        if (ev.target.name !== 'mandar-otro') return;
        grabadoElegido = { id: Number(ev.target.value), of_origen: ev.target.dataset.of };
        pintarGrabadoElegido(grabadoElegido.of_origen === String(filaActiva.of)
            ? 'Grabado de esta misma OF.' : 'Elegiste otro grabado aprobado del mismo proceso.');
    });

    $('recoger-btn-cancelar').addEventListener('click', () => cerrarModal('modal-recoger'));
    $('recoger-btn-confirmar').addEventListener('click', confirmarRecoger);
    document.querySelectorAll('input[name="estado_fisico"]').forEach(r =>
        r.addEventListener('change', actualizarComentarioObligatorio));

    document.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Escape') return;
        if ($('modal-mandar').style.display === 'flex') cerrarModal('modal-mandar');
        else if ($('modal-recoger').style.display === 'flex') cerrarModal('modal-recoger');
    });

    renderizarTabla();
})();

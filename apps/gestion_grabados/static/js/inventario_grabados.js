/* ============================================================
   Inventario de Grabados — ver views_grabados.py (api_inventario)
   El detalle de cada grabado se abre con el componente reutilizable
   PanelGrabado (static/js/componentes/panel_grabado.js), solo consulta.
   La ubicación se edita con doble clic en su celda (api_grabado_ubicacion):
   Enter guarda, Esc cancela; el servidor valida el formato (ej. GBB-2-1).
   ============================================================ */

(function () {
    'use strict';

    const filtros = { q: '', proceso: '', tipo: '', estado: '' };
    let grabados = [];
    let pedidoActual = 0;              // descarta respuestas viejas al escribir rápido

    const $ = (id) => document.getElementById(id);
    const cuerpo = $('inv-cuerpo');
    const tarjetas = [...document.querySelectorAll('.inv-tarjeta')];

    const COLUMNAS = 13;

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function escapar(valor) {
        const div = document.createElement('div');
        div.textContent = valor === null || valor === undefined || valor === '' ? '—' : String(valor);
        return div.innerHTML;
    }

    function etiqueta(codigo, texto) {
        if (!codigo) return '—';
        return `<span class="pg-etiqueta pg-etiqueta--${escapar(codigo)}">${escapar(texto)}</span>`;
    }

    function debounce(fn, espera) {
        let temporizador;
        return (...args) => { clearTimeout(temporizador); temporizador = setTimeout(() => fn(...args), espera); };
    }

    function mensajeTabla(texto) {
        cuerpo.innerHTML = `<tr><td colspan="${COLUMNAS}" class="tabla-sin-resultados">${escapar(texto)}</td></tr>`;
    }

    // ---------------------------------------------------------------- render
    function renderizarTarjetas(conteos) {
        tarjetas.forEach(t => {
            const estado = t.dataset.estado;
            $(`inv-conteo-${estado}`).textContent = conteos[estado] ?? 0;
            t.setAttribute('aria-pressed', String(filtros.estado === estado));
        });

        const activo = $('inv-filtro-activo');
        if (filtros.estado) {
            const tarjeta = tarjetas.find(t => t.dataset.estado === filtros.estado);
            $('inv-quitar-estado').textContent = `${tarjeta.querySelector('.inv-tarjeta__nombre').textContent} ✕`;
            activo.style.display = 'inline-flex';
        } else {
            activo.style.display = 'none';
        }
    }

    function renderizarTabla(res) {
        $('inv-contador').textContent = `Mostrando ${res.data.length} de ${res.total}`;
        const aviso = $('inv-aviso-limite');
        if (res.total > res.data.length) {
            aviso.textContent = `Se muestran los ${res.data.length} grabados modificados más recientemente de ${res.total}. ` +
                'Afina la búsqueda o filtra por estado, proceso o tipo para ver el resto.';
            aviso.style.display = 'block';
        } else {
            aviso.style.display = 'none';
        }

        if (!grabados.length) {
            mensajeTabla(filtros.q || filtros.proceso || filtros.tipo || filtros.estado
                ? 'Ningún grabado coincide con los filtros.'
                : 'Todavía no hay grabados registrados.');
            return;
        }
        cuerpo.innerHTML = grabados.map(g => `
            <tr class="inv-fila" tabindex="0" data-id="${g.id}"
                aria-label="Ver detalle del grabado ${escapar(g.of_origen)} ${escapar(g.proceso)}">
                <td data-label="OF de origen"><strong>${escapar(g.of_origen)}</strong></td>
                <td data-label="Proceso">${escapar(g.proceso)}</td>
                <td data-label="Cliente">${escapar(g.cliente)}</td>
                <td data-label="Referencia" class="inv-referencia">${escapar(g.referencia)}</td>
                <td data-label="Estado">${etiqueta(g.estado, g.estado_display)}</td>
                <td data-label="Tipo">${etiqueta('tipo-' + g.tipo, g.tipo_display)}</td>
                <td data-label="Intentos K1" class="inv-centrado">${escapar(g.intentos_k1)}</td>
                <td data-label="Último K1">${etiqueta(g.ultimo_k1, g.ultimo_k1_display)}</td>
                <td data-label="Usos" class="inv-centrado">${escapar(g.usos_acumulados)}</td>
                <td data-label="Ubicación" class="inv-ubicacion" tabindex="0"
                    title="Doble clic para editar la ubicación">${escapar(g.ubicacion)}</td>
                <td data-label="Última modificación" class="inv-modificado">${escapar(g.actualizado_el)}</td>
                <td data-label="Fecha de creación">${escapar(g.creado_el)}</td>
                <td data-label="Acciones" class="inv-centrado">
                    <!-- La etiqueta se imprime en el paso 3; por ahora el botón solo queda preparado. -->
                    <button type="button" class="inv-imprimir" data-id="${g.id}" disabled
                            title="Disponible próximamente" aria-label="Imprimir etiqueta (disponible próximamente)">
                        <svg viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="6 9 6 2 18 2 18 9"/>
                            <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>
                            <rect x="6" y="14" width="12" height="8"/>
                        </svg>
                    </button>
                </td>
            </tr>`).join('');
    }

    // ----------------------------------------------------------------- datos
    function cargar() {
        const pedido = ++pedidoActual;
        const params = new URLSearchParams();
        Object.entries(filtros).forEach(([clave, valor]) => { if (valor) params.set(clave, valor); });

        fetch(`/grabados/api/inventario/?${params}`)
            .then(r => r.json())
            .then(res => {
                if (pedido !== pedidoActual) return;
                if (res.status !== 'ok') { mensajeTabla(res.message || 'No se pudo cargar el inventario.'); return; }
                grabados = res.data;
                renderizarTarjetas(res.conteos);
                renderizarTabla(res);
            })
            .catch(() => { if (pedido === pedidoActual) mensajeTabla('Error de conexión al cargar el inventario.'); });
    }

    function abrirDetalle(id) {
        window.PanelGrabado.abrir(id);   // solo consulta: sin botones de decisión
    }

    // ------------------------------------------------------ editar ubicación
    function grabadoDeFila(fila) {
        return grabados.find(g => g.id === Number(fila.dataset.id));
    }

    function editarUbicacion(celda) {
        if (celda.querySelector('input')) return;
        const fila = celda.closest('.inv-fila');
        const grabado = grabadoDeFila(fila);
        // Un solo hijo: en celular la celda es flex (etiqueta a un lado, valor al otro).
        celda.innerHTML = `<div>
            <input type="text" class="inv-ubicacion__input" maxlength="20" autocomplete="off"
                   placeholder="Ej: GBB-2-1" aria-label="Nueva ubicación">
            <div class="inv-ubicacion__error" role="alert"></div></div>`;
        const input = celda.querySelector('input');
        input.value = grabado.ubicacion || '';
        input.focus();
        input.select();

        let guardando = false;
        const cerrar = () => { celda.innerHTML = escapar(grabado.ubicacion); };
        const mostrarError = (texto) => { celda.querySelector('.inv-ubicacion__error').textContent = texto; };

        input.addEventListener('keydown', (e) => {
            e.stopPropagation();   // Enter no abre el panel de detalle
            if (e.key === 'Escape') { e.preventDefault(); cerrar(); celda.focus(); }
            if (e.key !== 'Enter' || guardando) return;
            e.preventDefault();
            guardando = true;
            input.disabled = true;
            mostrarError('');
            fetch(`/grabados/api/grabado/${grabado.id}/ubicacion/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
                body: JSON.stringify({ ubicacion: input.value }),
            })
                .then(r => r.json())
                .then(res => {
                    if (res.status !== 'ok') throw new Error(res.message || 'No se pudo guardar la ubicación.');
                    grabado.ubicacion = res.ubicacion;
                    grabado.actualizado_el = res.actualizado_el;
                    fila.querySelector('.inv-modificado').innerHTML = escapar(res.actualizado_el);
                    cerrar();
                    celda.focus();
                })
                .catch(err => {
                    guardando = false;
                    input.disabled = false;
                    input.focus();
                    mostrarError(err.message === 'Failed to fetch' ? 'Error de conexión. Inténtalo de nuevo.' : err.message);
                });
        });
        // Salir sin Enter cancela (mientras no se esté guardando).
        input.addEventListener('blur', () => { if (!guardando) cerrar(); });
    }

    // --------------------------------------------------------------- eventos
    tarjetas.forEach(t => t.addEventListener('click', () => {
        // Clic en la tarjeta activa = quitar el filtro.
        filtros.estado = filtros.estado === t.dataset.estado ? '' : t.dataset.estado;
        cargar();
    }));

    $('inv-quitar-estado').addEventListener('click', () => { filtros.estado = ''; cargar(); });

    $('inv-buscador').addEventListener('input', debounce((e) => {
        filtros.q = e.target.value.trim();
        cargar();
    }, 300));

    $('inv-proceso').addEventListener('change', (e) => { filtros.proceso = e.target.value; cargar(); });
    $('inv-tipo').addEventListener('change', (e) => { filtros.tipo = e.target.value; cargar(); });

    cuerpo.addEventListener('click', (e) => {
        // La celda de ubicación (doble clic = editar) y las acciones no abren el panel.
        if (e.target.closest('.inv-ubicacion, .inv-imprimir')) return;
        const fila = e.target.closest('.inv-fila');
        if (fila) abrirDetalle(Number(fila.dataset.id));
    });

    cuerpo.addEventListener('dblclick', (e) => {
        const celda = e.target.closest('.inv-ubicacion');
        if (celda) editarUbicacion(celda);
    });

    cuerpo.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        if (e.target.classList.contains('inv-fila')) {
            e.preventDefault();
            abrirDetalle(Number(e.target.dataset.id));
        } else if (e.target.classList.contains('inv-ubicacion')) {
            e.preventDefault();
            editarUbicacion(e.target);   // con teclado: Enter sobre la celda
        }
    });

    cargar();
})();

/* ============================================================
   Inventario de Grabados — ver views_grabados.py (api_inventario)
   El detalle de cada grabado se abre con el componente reutilizable
   PanelGrabado (static/js/componentes/panel_grabado.js), solo consulta.
   ============================================================ */

(function () {
    'use strict';

    const filtros = { q: '', proceso: '', estado: '' };
    let grabados = [];
    let pedidoActual = 0;              // descarta respuestas viejas al escribir rápido

    const $ = (id) => document.getElementById(id);
    const cuerpo = $('inv-cuerpo');
    const tarjetas = [...document.querySelectorAll('.inv-tarjeta')];

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
        cuerpo.innerHTML = `<tr><td colspan="10" class="tabla-sin-resultados">${escapar(texto)}</td></tr>`;
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
        $('inv-contador').textContent = `${res.total} grabado(s)`;
        const aviso = $('inv-aviso-limite');
        if (res.total > res.data.length) {
            aviso.textContent = `Se muestran los ${res.data.length} grabados más recientes de ${res.total}. ` +
                'Afina la búsqueda o filtra por estado o proceso para ver el resto.';
            aviso.style.display = 'block';
        } else {
            aviso.style.display = 'none';
        }

        if (!grabados.length) {
            mensajeTabla(filtros.q || filtros.proceso || filtros.estado
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
                <td data-label="Intentos K1" class="inv-centrado">${escapar(g.intentos_k1)}</td>
                <td data-label="Último K1">${etiqueta(g.ultimo_k1, g.ultimo_k1_display)}</td>
                <td data-label="Usos" class="inv-centrado">${escapar(g.usos_acumulados)}</td>
                <td data-label="Ubicación">${escapar(g.ubicacion)}</td>
                <td data-label="Fecha de alta">${escapar(g.creado_el)}</td>
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

    cuerpo.addEventListener('click', (e) => {
        const fila = e.target.closest('.inv-fila');
        if (fila) abrirDetalle(Number(fila.dataset.id));
    });

    cuerpo.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.classList.contains('inv-fila')) {
            e.preventDefault();
            abrirDetalle(Number(e.target.dataset.id));
        }
    });

    cargar();
})();

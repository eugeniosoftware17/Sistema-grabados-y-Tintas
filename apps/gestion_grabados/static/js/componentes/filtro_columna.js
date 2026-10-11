/* ============================================================
   FiltroColumna: filtros por columna (embudo en el encabezado) con el panel
   flotante de casillas (incluir/excluir, buscador y "(Seleccionar todo)") o
   rango de fechas desde/hasta. Lo usan el Inventario, Sobres y el PLANI.

   La pantalla pone en su HTML los botones .boton-filtro-columna
   (data-columna, data-nombre), un punto por columna, el botón "Quitar filtros
   de columna" y el panel (#filtro-columna-panel con su título, Limpiar y lista).
   Estilos en grabados_tabla.css (.filtro-columna-*, .fc-*).

   const filtro = FiltroColumna.crear({
       ids: { panel, lista, titulo, limpiar, quitarTodos },   // ids del HTML
       idPunto: col => 'inv-punto-' + col,
       columnasFecha: ['creado'], columnasNumericas: ['usos'],
       obtenerValores: col => Promise<{ valores: [...], etiquetas: {código: texto} }>,
       alCambiar: () => recargar(),
   });
   filtro.hay()           ¿hay algún filtro de columna?
   filtro.paraServidor()  lista en el formato de selectors._aplicar_filtros_consulta
   filtro.cumple(col, v)  para filtrar en la pantalla (v: valor mostrado; en
                          las columnas de fecha, 'aaaa-mm-dd' o null)
   ============================================================ */

(function () {
    'use strict';

    const VACIO = '—';

    function escapar(valor) {
        const div = document.createElement('div');
        div.textContent = valor === null || valor === undefined || valor === '' ? VACIO : String(valor);
        return div.innerHTML;
    }

    function normalizarValor(v) {
        return v === null || v === undefined || v === '' || v === VACIO ? VACIO : String(v);
    }

    function crear(opciones) {
        const $ = (id) => document.getElementById(id);
        const ids = opciones.ids;
        const columnasFecha = opciones.columnasFecha || [];
        const columnasNumericas = opciones.columnasNumericas || [];
        const filtros = {};        // { estado: Set(['APROBADO']), creado: {desde, hasta} } — sin entrada = sin filtro
        const valores = {};        // { cliente: ['A', 'B'] } — opciones del filtro
        const etiquetas = {};      // { estado: { APROBADO: 'Aprobado' } } — código -> texto de pantalla
        let columnaAbierta = null;

        const panel = $(ids.panel);
        const lista = $(ids.lista);
        const botones = [...document.querySelectorAll('.boton-filtro-columna')];

        function hay() {
            return Object.keys(filtros).length > 0;
        }

        function textoValor(col, v) {
            return (etiquetas[col] || {})[v] || v;
        }

        // De una lista se manda la parte más corta: los marcados ('in') o los desmarcados ('fuera').
        function paraServidor() {
            return Object.entries(filtros).map(([col, filtro]) => {
                if (columnasFecha.includes(col)) return { col, desde: filtro.desde || null, hasta: filtro.hasta || null };
                const marcados = [...filtro];
                const desmarcados = (valores[col] || []).filter(v => !filtro.has(v));
                return valores[col] && desmarcados.length < marcados.length
                    ? { col, fuera: desmarcados }
                    : { col, in: marcados };
            });
        }

        function cumple(col, valor) {
            const filtro = filtros[col];
            if (!filtro) return true;
            if (columnasFecha.includes(col)) {
                // Sin fecha no pasa un filtro de fechas (igual que en el servidor).
                if (!valor) return false;
                return (!filtro.desde || valor >= filtro.desde) && (!filtro.hasta || valor <= filtro.hasta);
            }
            return filtro.has(normalizarValor(valor));
        }

        function actualizarIndicadores() {
            botones.forEach(btn => {
                const activo = !!filtros[btn.dataset.columna];
                btn.classList.toggle('boton-filtro-columna--activo', activo);
                const punto = $(opciones.idPunto(btn.dataset.columna));
                if (punto) punto.classList.toggle('activo', activo);
            });
            $(ids.quitarTodos).classList.toggle('activo', hay());
        }

        function aplicar(col, filtro) {
            if (filtro) filtros[col] = filtro;
            else delete filtros[col];
            actualizarIndicadores();
            opciones.alCambiar();
        }

        function cargarValores(col) {
            return Promise.resolve(opciones.obtenerValores(col)).then(res => {
                etiquetas[col] = res.etiquetas || {};
                const lista = [...new Set((res.valores || []).map(normalizarValor))];
                // Un valor marcado que ya no sale (ej. cambió la búsqueda) se sigue mostrando.
                if (filtros[col]) filtros[col].forEach(v => { if (!lista.includes(v)) lista.push(v); });
                if (columnasNumericas.includes(col)) lista.sort((a, b) => (parseFloat(a) || 0) - (parseFloat(b) || 0));
                else lista.sort((a, b) => textoValor(col, a).localeCompare(textoValor(col, b), 'es'));
                valores[col] = lista;
                return lista;
            });
        }

        function cerrar() {
            panel.style.display = 'none';
            columnaAbierta = null;
        }

        function pintarFechas(col) {
            const filtro = filtros[col] || {};
            lista.innerHTML = `<div class="fc-fechas">
                <label>Desde:<input type="date" class="fc-fecha-desde"></label>
                <label>Hasta:<input type="date" class="fc-fecha-hasta"></label>
            </div>`;
            const desde = lista.querySelector('.fc-fecha-desde');
            const hasta = lista.querySelector('.fc-fecha-hasta');
            desde.value = filtro.desde || '';
            hasta.value = filtro.hasta || '';
            const alCambiar = () => aplicar(col, desde.value || hasta.value ? { desde: desde.value, hasta: hasta.value } : null);
            desde.addEventListener('change', alCambiar);
            hasta.addEventListener('change', alCambiar);
        }

        function pintarCasillas(col, opcionesCol) {
            const seleccion = filtros[col];  // Set o undefined (= todos)
            lista.innerHTML = `
                <div class="fc-buscador">
                    <input type="text" class="fc-buscar" placeholder="Buscar valor..." autocomplete="off">
                </div>
                <div class="filtro-columna-opcion filtro-columna-opcion--todos">
                    <input type="checkbox" id="fc-todos" ${seleccion ? '' : 'checked'}>
                    <label for="fc-todos">(Seleccionar todo)</label>
                </div>
                ${opcionesCol.map((v, i) => `
                <div class="filtro-columna-opcion fc-fila" data-texto="${escapar(textoValor(col, v).toLowerCase())}">
                    <input type="checkbox" class="fc-valor" id="fc-valor-${i}" value="${escapar(v)}"
                           ${!seleccion || seleccion.has(v) ? 'checked' : ''}>
                    <label for="fc-valor-${i}" title="${escapar(textoValor(col, v))}">${escapar(textoValor(col, v))}</label>
                </div>`).join('')}`;
            const casillas = [...lista.querySelectorAll('.fc-valor')];
            const todos = lista.querySelector('#fc-todos');
            const alCambiar = () => {
                const marcados = casillas.filter(c => c.checked).map(c => c.value);
                todos.checked = marcados.length === casillas.length;
                aplicar(col, marcados.length === casillas.length ? null : new Set(marcados));
            };
            casillas.forEach(c => c.addEventListener('change', alCambiar));
            todos.addEventListener('change', (e) => {
                // Solo cambia los valores visibles (los que deja el buscador del panel).
                casillas.forEach(c => { if (c.closest('.fc-fila').style.display !== 'none') c.checked = e.target.checked; });
                alCambiar();
            });
            const buscar = lista.querySelector('.fc-buscar');
            buscar.addEventListener('input', (e) => {
                const texto = e.target.value.trim().toLowerCase();
                lista.querySelectorAll('.fc-fila').forEach(fila => {
                    fila.style.display = !texto || fila.dataset.texto.includes(texto) ? '' : 'none';
                });
            });
            buscar.focus();
        }

        function abrir(btn) {
            const col = btn.dataset.columna;
            if (panel.style.display === 'block' && columnaAbierta === col) { cerrar(); return; }
            columnaAbierta = col;
            $(ids.titulo).textContent = 'Filtrar ' + btn.dataset.nombre;
            const rect = btn.getBoundingClientRect();
            panel.style.top = `${rect.bottom + 4}px`;
            panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 236))}px`;
            panel.style.display = 'block';

            if (columnasFecha.includes(col)) { pintarFechas(col); return; }

            lista.innerHTML = '<div class="fc-mensaje">Cargando valores...</div>';
            cargarValores(col).then(opcionesCol => {
                if (columnaAbierta === col) pintarCasillas(col, opcionesCol);   // si no, ya se cerró o se abrió otra
            }).catch(() => {
                if (columnaAbierta === col) {
                    lista.innerHTML = '<div class="fc-mensaje">No se pudieron cargar los valores. Inténtalo de nuevo.</div>';
                }
            });
        }

        // --------------------------------------------------------------- eventos
        botones.forEach(btn => btn.addEventListener('click', (e) => {
            e.stopPropagation();
            abrir(btn);
        }));

        $(ids.limpiar).addEventListener('click', () => {
            const col = columnaAbierta;
            if (!col) return;
            if (columnasFecha.includes(col)) lista.querySelectorAll('input[type="date"]').forEach(c => { c.value = ''; });
            else lista.querySelectorAll('input[type="checkbox"]').forEach(c => { c.checked = true; });
            aplicar(col, null);
        });

        $(ids.quitarTodos).addEventListener('click', () => {
            Object.keys(filtros).forEach(col => delete filtros[col]);
            cerrar();
            actualizarIndicadores();
            opciones.alCambiar();
        });

        // Clic afuera o Esc cierran el panel.
        document.addEventListener('click', (e) => {
            if (!e.target.closest(`#${ids.panel}, .boton-filtro-columna`)) cerrar();
        });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrar(); });
        window.addEventListener('resize', cerrar);

        return { hay, paraServidor, cumple };
    }

    window.FiltroColumna = { crear, VACIO };
})();

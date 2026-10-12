
/* Tabla de Consultas ("Registro de Órdenes y Grabados"): una sola fuente, las tablas
   nuevas. Una fila por cada envío a máquina (EnvioMaquina) con los datos de su grabado,
   y una fila de creación por cada grabado que todavía no se ha mandado a máquina.
   - Todas las filas tienen grabado (reg.grabado): clic (o Enter) abre PanelGrabado.abrir.
   - Doble clic (o Enter) en Ubicación: edita la ubicación DEL GRABADO con
     api_grabado_ubicacion (origen CONSULTA, mismo servicio que el Inventario). Un
     grabado inactivo no se edita.
   - Búsqueda, filtros por columna, botón Fecha y tope de 500 van al servidor
     (api/consulta/registros/); la pantalla solo ordena lo que recibió.
   - Columnas: COLUMNAS (las de `defecto` se ven al entrar; las demás, en el selector
     de columnas). Un dato que no existe queda en blanco, salvo OF de origen y Máquina, que
     muestran "—"; `mostrar` es la clave que se ve y ordena (el filtro usa el valor real). */
(function() {
    'use strict';

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

    /* ============================================================
       1. COLUMNAS
       tipo: 'texto' | 'numero' | 'fecha' (dd/mm/aaaa) | 'fechahora' (dd/mm/aaaa hh:mm)
       Las de fecha se filtran por rango; las demás con casillas.
    ============================================================ */
    const COLUMNAS = [
      { id: 'of',            nombre: 'OF',                 defecto: true },
      { id: 'of_origen',     nombre: 'OF de origen',       defecto: true, mostrar: 'of_origen_mostrar' },
      { id: 'cliente',       nombre: 'Cliente',            defecto: true },
      { id: 'desc',          nombre: 'Descripción',        defecto: true },
      { id: 'proceso',       nombre: 'Proceso',            defecto: true },
      { id: 'estado',        nombre: 'Estado',             defecto: true, mostrar: 'estado_display' },
      { id: 'maquina',       nombre: 'Máquina',            defecto: true, mostrar: 'maquina_mostrar' },
      { id: 'ubicacion',     nombre: 'Ubicación',          defecto: true },
      { id: 'fecha',         nombre: 'Fecha prog.',        defecto: true, tipo: 'fecha' },
      { id: 'modificado',    nombre: 'Última modificación', defecto: true, tipo: 'fechahora' },
      { id: 'sobre',         nombre: 'Sobre' },
      { id: 'cantidad',      nombre: 'Cant. formatos',     tipo: 'numero' },
      { id: 'horas',         nombre: 'Horas de proceso',   tipo: 'numero' },
      { id: 'papel',         nombre: 'Papel' },
      { id: 'enviado_el',    nombre: 'Enviado el',         tipo: 'fechahora' },
      { id: 'enviado_por',   nombre: 'Enviado por' },
      { id: 'recogido_el',   nombre: 'Recogido el',        tipo: 'fechahora' },
      { id: 'recogido_por',  nombre: 'Recogido por' },
      { id: 'estado_fisico', nombre: 'Estado físico',      mostrar: 'estado_fisico_display' },
      { id: 'tipo',          nombre: 'Tipo',               mostrar: 'tipo_display' },
      { id: 'usos',          nombre: 'Usos',               tipo: 'numero' },
    ];
    const COLUMNA = Object.fromEntries(COLUMNAS.map(c => [c.id, c]));
    const ES_FECHA = (col) => ['fecha', 'fechahora'].includes((COLUMNA[col] || {}).tipo);
    const CLAVE_COLUMNAS = 'consulta_columnas_visibles';   // preferencia de este navegador
    const VACIO = '—';   // así se ve (y se filtra) el valor vacío en la lista del filtro

    const CLASE_POR_ESTADO = {
      'EN_FABRICACION': 'estado--en-proceso',
      'PENDIENTE_K1'  : 'estado--en-revision',
      'APROBADO'      : 'estado--completado',
      'EN_MAQUINA'    : 'estado--en-proceso',
      'REPETIR'       : 'estado--cancelado',
      'INACTIVO'      : 'estado--inactivo'
    };

    const SVG_CANDADO = `<svg viewBox="0 0 24 24" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>`;
    const SVG_PIN = `<svg viewBox="0 0 24 24" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`;
    const SVG_ORDEN = `<svg viewBox="0 0 24 24" stroke-width="2"><path d="m7 15 5 5 5-5"/><path d="m7 9 5-5 5 5"/></svg>`;
    const SVG_EMBUDO = `<svg viewBox="0 0 24 24" stroke-width="2"><polygon points="4 4 20 4 14 12 14 19 10 21 10 12 4 4"/></svg>`;

    /* ============================================================
       2. ESTADO DE LA PANTALLA
    ============================================================ */
    const REGISTROS_POR_PAGINA = 1000;

    let DATOS_REGISTROS    = [];
    let registrosFiltrados = [];
    let paginaActual       = 1;
    let palabrasBusqueda   = [];

    let visibles = new Set(COLUMNAS.filter(c => c.defecto).map(c => c.id));
    let filtrosColumna    = {}; // { estado: Set(['APROBADO']), fecha: {desde, hasta} } — sin entrada = sin filtro
    let columnaFiltroAbierta = null;
    let valoresColumna    = {}; // opciones del filtro (de todo el historial)
    let etiquetasColumna  = {}; // { estado: { APROBADO: 'Aprobado', ... } }
    let totalServidor     = 0;  // filas que cumplen búsqueda + filtros (puede pasar del tope de 500)
    let consultaActual    = 0;  // descarta respuestas viejas si el usuario sigue filtrando

    // Sin filtro de fecha por defecto: el botón Fecha solo filtra si el usuario lo usa.
    let fechaDesdeTs       = null;
    let fechaHastaTs       = null;
    let columnaOrden       = null;
    let direccionOrden     = 'asc';

    /* ============================================================
       3. UTILIDADES
    ============================================================ */

    // Texto de una celda: lo que no existe queda en blanco (sin '—' ni 0 inventado).
    function textoCelda(valor) {
      return valor === null || valor === undefined ? '' : String(valor);
    }

    // OF de origen y Máquina: sin valor para mostrar se ve "—" (celda y tooltip). Solo es
    // presentación: el orden sigue usando el valor vacío y los filtros, el valor real.
    function conRaya(valor) {
      return valor === null || valor === undefined || valor === '' ? VACIO : valor;
    }

    function escaparHtml(valor) {
      const div = document.createElement('div');
      div.textContent = textoCelda(valor);
      return div.innerHTML;
    }

    // Texto completo para el title de una celda cortada con "..." (.celda-texto).
    function tituloCelda(valor) {
      return escaparHtml(valor).replace(/"/g, '&quot;');
    }

    function resaltarTexto(valor, palabras) {
      let res = escaparHtml(valor);
      if (!res || !palabras || palabras.length === 0) return res;
      palabras.forEach(p => {
        const seguro = escaparHtml(p).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        res = res.replace(new RegExp(`(${seguro})`, 'gi'), '<mark class="texto-resaltado">$1</mark>');
      });
      return res;
    }

    // 'dd/mm/aaaa' o 'dd/mm/aaaa hh:mm' -> timestamp (para ordenar)
    function parsearFecha(str) {
      const m = /^(\d{2})\/(\d{2})\/(\d{4})(?: (\d{2}):(\d{2}))?$/.exec(str || '');
      return m ? new Date(m[3], m[2] - 1, m[1], m[4] || 0, m[5] || 0).getTime() : null;
    }

    function formatearFechaInputLocal(ts) {
      const d = new Date(ts);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    /* ============================================================
       4. CARGA (todo filtra en el servidor)
    ============================================================ */

    function cargarKPIs() {
      fetch('/grabados/api/kpis/')
        .then(res => res.json())
        .then(kpis => {
            document.getElementById('kpi-maquina').innerText = kpis.en_maquina || 0;
            document.getElementById('kpi-hoy').innerText = kpis.completados_hoy || 0;
            document.getElementById('kpi-repetir').innerText = kpis.para_repetir || 0;
            document.getElementById('kpi-pendientes').innerText = kpis.pendientes || 0;
        });
    }

    // Filtros en el formato de selectors._aplicar_filtros_consulta.
    function filtrosParaServidor() {
      const filtros = [];
      const estado = document.getElementById('filtro-estado').value;
      const proceso = document.getElementById('filtro-proceso').value;
      if (estado) filtros.push({ col: 'estado', in: [estado] });
      if (proceso) filtros.push({ col: 'proceso', in: [proceso] });
      if (fechaDesdeTs || fechaHastaTs) {
        filtros.push({ col: 'fecha',
                       desde: fechaDesdeTs ? formatearFechaInputLocal(fechaDesdeTs) : null,
                       hasta: fechaHastaTs ? formatearFechaInputLocal(fechaHastaTs) : null });
      }
      for (const { id: col } of COLUMNAS) {
        const filtro = filtrosColumna[col];
        if (!filtro) continue;
        if (ES_FECHA(col)) {
          filtros.push({ col,
                         desde: filtro.desde ? formatearFechaInputLocal(filtro.desde) : null,
                         hasta: filtro.hasta ? formatearFechaInputLocal(filtro.hasta) : null });
        } else {
          // Se manda la lista más corta: los marcados o los desmarcados.
          const marcados = Array.from(filtro);
          const desmarcados = (valoresColumna[col] || []).filter(v => !filtro.has(v));
          filtros.push(valoresColumna[col] && desmarcados.length < marcados.length
            ? { col, fuera: desmarcados } : { col, in: marcados });
        }
      }
      return filtros;
    }

    function cargarRegistros() {
      const params = new URLSearchParams();
      const texto = palabrasBusqueda.join(' ');
      if (texto) params.set('q', texto);
      const filtros = filtrosParaServidor();
      if (filtros.length) params.set('filtros', JSON.stringify(filtros));
      const consulta = ++consultaActual;
      return fetch(`/grabados/api/consulta/registros/?${params}`)
        .then(res => res.json())
        .then(res => {
          if (consulta !== consultaActual) return;  // ya hay una consulta más nueva
          if (res.status !== 'ok') throw new Error(res.message || 'No se pudieron cargar los registros.');
          DATOS_REGISTROS = res.data;
          totalServidor = res.total;
          ordenarYMostrar();
        })
        .catch(err => console.error('Error al cargar registros:', err));
    }
    const recargarRegistros = debounce(cargarRegistros, 250);
    window.recargarRegistros = recargarRegistros;

    function cargarDatosDesdeDB() {
      cargarKPIs();
      cargarRegistros();
    }

    function valorParaOrdenar(reg, col) {
      const columna = COLUMNA[col];
      const valor = reg[columna.mostrar || col];
      if (valor === null || valor === undefined || valor === '') return null;
      if (ES_FECHA(col)) return parsearFecha(valor);
      if (columna.tipo === 'numero') return Number(valor);
      return String(valor).toLowerCase();
    }

    // Sin orden elegido queda el del servidor: la fila más reciente primero.
    function ordenarYMostrar() {
      const filas = DATOS_REGISTROS.slice();
      if (columnaOrden) {
        const signo = direccionOrden === 'asc' ? 1 : -1;
        filas.sort((a, b) => {
          const valA = valorParaOrdenar(a, columnaOrden), valB = valorParaOrdenar(b, columnaOrden);
          if (valA === valB) return 0;
          if (valA === null) return 1;      // vacíos siempre al final
          if (valB === null) return -1;
          return (valA < valB ? -1 : 1) * signo;
        });
      }
      registrosFiltrados = filas;
      paginaActual = 1;
      actualizarUI();
    }

    function actualizarUI() {
      renderizarFilas();
      renderizarPaginacion(registrosFiltrados.length);
      const info = document.getElementById('pie-info');
      if (info) {
        const aviso = totalServidor > DATOS_REGISTROS.length ? ' · afina la búsqueda o los filtros para ver otros' : '';
        info.innerHTML = `Mostrando <strong>${registrosFiltrados.length}</strong> de <strong>${totalServidor}</strong> registros${aviso}`;
      }
    }

    /* ============================================================
       5. RENDERIZADO
    ============================================================ */

    function renderizarEncabezado() {
      const fila = document.getElementById('tabla-encabezado');
      fila.innerHTML = COLUMNAS.map(c => `
        <th data-col="${c.id}">${c.nombre} ${SVG_ORDEN}
          <button type="button" class="boton-filtro-columna" data-columna="${c.id}" title="Filtrar ${c.nombre}">${SVG_EMBUDO}</button>
          <span id="indicador-filtro-${c.id}" class="indicador-filtro-columna" style="display:none;"></span>
        </th>`).join('') + '<th data-col="acciones">Acciones</th>';
    }

    function celda(col, contenido, extra = '') {
      return `<td data-col="${col}" data-label="${COLUMNA[col].nombre}" ${extra}>${contenido}</td>`;
    }

    function celdaTexto(col, valor, clase = '') {
      return celda(col, `<span class="celda-texto ${clase}" title="${tituloCelda(valor)}">${resaltarTexto(valor, palabrasBusqueda)}</span>`);
    }

    const MOTIVO_GRABADO_INACTIVO = 'Grabado inactivo: reactívalo desde su panel para cambiar la ubicación.';

    function grabadoInactivo(reg) {
      return reg.grabado.estado === 'INACTIVO';
    }

    function htmlCeldaUbicacion(reg) {
      if (grabadoInactivo(reg)) {
        return celda('ubicacion', `<span class="celda-ubicacion">${SVG_CANDADO}${resaltarTexto(reg.ubicacion, palabrasBusqueda)}</span>`,
                     `class="consulta-ubicacion consulta-ubicacion--bloqueada" tabindex="0" title="${escaparHtml(MOTIVO_GRABADO_INACTIVO)}"`);
      }
      return celda('ubicacion', `<span class="celda-ubicacion">${SVG_PIN}${resaltarTexto(reg.ubicacion, palabrasBusqueda)}</span>`,
                   `class="consulta-ubicacion" tabindex="0" title="Ubicación del grabado ${escaparHtml(reg.grabado.of_origen)} ${escaparHtml(reg.grabado.proceso)}. Doble clic para editarla"`);
    }

    function htmlFila(reg, idx) {
      const p = palabrasBusqueda;
      const acciones = `<div class="fila-acciones">
            <button class="boton-accion boton-accion--ver" onclick="window.abrirDetalleConsulta(${idx})" title="Ver detalle">
                <svg viewBox="0 0 24 24" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
            <button type="button" class="boton-accion boton-accion--etiqueta" data-etiqueta="${reg.grabado.id}"
                    title="Imprimir etiqueta del grabado ${escaparHtml(reg.grabado.of_origen)} ${escaparHtml(reg.grabado.proceso)}">${window.Etiqueta.BOTON_SVG}</button>
          </div>`;
      const celdas = {
        of:            celda('of', `<span class="celda-of-enlace">${resaltarTexto(reg.of, p)}</span>`),
        // OF de origen y Máquina: lo que se ve ("—" si es la misma OF, una fila de creación o
        // la máquina comodín de la migración); el filtro y la búsqueda usan el valor real.
        of_origen:     celdaTexto('of_origen', conRaya(reg.of_origen_mostrar), 'celda-of-referencia'),
        cliente:       celdaTexto('cliente', reg.cliente, 'celda-cliente'),
        desc:          celdaTexto('desc', reg.desc, 'celda-descripcion'),
        proceso:       celda('proceso', resaltarTexto(reg.proceso, p)),
        estado:        celda('estado', `<span class="celda-estado ${CLASE_POR_ESTADO[reg.estado] || 'estado--pendiente'}">${escaparHtml(reg.estado_display)}</span>`),
        maquina:       celdaTexto('maquina', conRaya(reg.maquina_mostrar), 'celda-maquina'),
        ubicacion:     htmlCeldaUbicacion(reg),
        fecha:         celda('fecha', escaparHtml(reg.fecha)),
        modificado:    celda('modificado', escaparHtml(reg.modificado), 'class="consulta-modificado"'),
        sobre:         celdaTexto('sobre', reg.sobre),
        cantidad:      celda('cantidad', escaparHtml(reg.cantidad)),
        horas:         celda('horas', escaparHtml(reg.horas)),
        papel:         celdaTexto('papel', reg.papel),
        enviado_el:    celda('enviado_el', escaparHtml(reg.enviado_el), 'class="consulta-modificado"'),
        enviado_por:   celda('enviado_por', escaparHtml(reg.enviado_por)),
        recogido_el:   celda('recogido_el', escaparHtml(reg.recogido_el), 'class="consulta-modificado"'),
        recogido_por:  celda('recogido_por', escaparHtml(reg.recogido_por)),
        estado_fisico: celda('estado_fisico', escaparHtml(reg.estado_fisico_display)),
        tipo:          celda('tipo', escaparHtml(reg.tipo_display)),
        usos:          celda('usos', escaparHtml(reg.usos)),
      };
      return `<tr class="consulta-fila" tabindex="0" data-idx="${idx}">
          ${COLUMNAS.map(c => celdas[c.id]).join('')}
          <td data-col="acciones">${acciones}</td>
        </tr>`;
    }

    function renderizarFilas() {
      const inicio = (paginaActual - 1) * REGISTROS_POR_PAGINA;
      const pagina = registrosFiltrados.slice(inicio, inicio + REGISTROS_POR_PAGINA);
      const tbody  = document.getElementById('tabla-cuerpo');
      if (!tbody) return;
      tbody.innerHTML = pagina.length
        ? pagina.map((reg, i) => htmlFila(reg, inicio + i)).join('')
        : `<tr><td colspan="${COLUMNAS.length + 1}" class="tabla-sin-resultados">No se encontraron registros.</td></tr>`;
      aplicarVisibilidadColumnas();
    }

    function renderizarPaginacion(total) {
      const paginas = Math.ceil(total / REGISTROS_POR_PAGINA);
      const contenedor = document.getElementById('pie-paginacion');
      if (!contenedor) return;
      let html = '';
      for (let i = 1; i <= paginas; i++) {
        if (i === 1 || i === paginas || Math.abs(i - paginaActual) <= 1) {
          html += `<button class="paginacion-boton ${i === paginaActual ? 'paginacion-boton--activo' : ''}" onclick="window.irAPagina(${i})">${i}</button>`;
        }
      }
      contenedor.innerHTML = html;
    }

    /* ============================================================
       6. SELECTOR DE COLUMNAS (la elección se recuerda en este navegador)
    ============================================================ */

    function leerColumnasGuardadas() {
      try {
        const guardadas = JSON.parse(localStorage.getItem(CLAVE_COLUMNAS) || 'null');
        if (Array.isArray(guardadas)) visibles = new Set(guardadas.filter(id => COLUMNA[id]));
      } catch (e) { /* sin almacenamiento: columnas por defecto */ }
      visibles.add('of');
    }

    function guardarColumnas() {
      try { localStorage.setItem(CLAVE_COLUMNAS, JSON.stringify(Array.from(visibles))); } catch (e) { /* nada */ }
    }

    function inicializarPanelColumnas() {
      const lista = document.getElementById('columnas-lista');
      if (!lista) return;
      lista.innerHTML = COLUMNAS.map(c => {
        const fija = c.id === 'of';
        return `<div class="columna-opcion ${fija ? 'columna-opcion--fija' : ''}">
            <input type="checkbox" id="chk-col-${c.id}" ${visibles.has(c.id) ? 'checked' : ''} ${fija ? 'disabled' : ''}
                   onchange="window.toggleColumna('${c.id}')">
            <label for="chk-col-${c.id}">${c.nombre}</label>
            ${fija ? '<span class="columna-opcion__fija">fijo</span>' : ''}
          </div>`;
      }).join('');
    }

    function aplicarVisibilidadColumnas() {
      COLUMNAS.forEach(c => {
        document.querySelectorAll(`[data-col="${c.id}"]`).forEach(el => {
          el.style.display = visibles.has(c.id) ? '' : 'none';
        });
      });
    }

    window.toggleColumna = function(id) {
      const chk = document.getElementById(`chk-col-${id}`);
      if (chk && chk.checked) visibles.add(id); else visibles.delete(id);
      aplicarVisibilidadColumnas();
      guardarColumnas();
    };

    window.restablecerColumnas = function() {
      visibles = new Set(COLUMNAS.filter(c => c.defecto).map(c => c.id));
      COLUMNAS.forEach(c => {
        const chk = document.getElementById(`chk-col-${c.id}`);
        if (chk) chk.checked = visibles.has(c.id);
      });
      aplicarVisibilidadColumnas();
      try { localStorage.removeItem(CLAVE_COLUMNAS); } catch (e) { /* nada */ }
    };

    /* ============================================================
       7. FILTROS POR COLUMNA (estilo Excel, en el servidor)
    ============================================================ */

    function normalizarValorColumna(v) {
      return (v === null || v === undefined || v === '' || v === VACIO) ? VACIO : String(v);
    }

    function etiquetaValor(col, v) {
      return (etiquetasColumna[col] || {})[v] || v;
    }

    // Opciones del filtro: valores de esa columna en todo el historial (respetando la búsqueda).
    function obtenerValoresUnicos(col) {
      const params = new URLSearchParams({ col });
      const texto = palabrasBusqueda.join(' ');
      if (texto) params.set('q', texto);
      return fetch(`/grabados/api/consulta/valores/?${params}`)
        .then(res => res.json())
        .then(res => {
          if (res.status !== 'ok') throw new Error(res.message);
          etiquetasColumna[col] = res.etiquetas || {};
          const valores = ordenarValores(col, Array.from(new Set(res.valores.map(normalizarValorColumna))));
          // Un valor marcado que ya no sale (ej. cambió la búsqueda) se sigue mostrando.
          if (filtrosColumna[col]) filtrosColumna[col].forEach(v => { if (!valores.includes(v)) valores.push(v); });
          valoresColumna[col] = valores;
          return valores;
        });
    }

    function ordenarValores(col, valores) {
      if (COLUMNA[col].tipo === 'numero') {
        valores.sort((a, b) => (parseFloat(a) || 0) - (parseFloat(b) || 0));
      } else {
        valores.sort((a, b) => etiquetaValor(col, a).localeCompare(etiquetaValor(col, b), 'es'));
      }
      return valores;
    }

    function actualizarIconoFiltro(col) {
      const btn = document.querySelector(`.boton-filtro-columna[data-columna="${col}"]`);
      if (btn) btn.classList.toggle('boton-filtro-columna--activo', !!filtrosColumna[col]);
      const punto = document.getElementById(`indicador-filtro-${col}`);
      if (punto) punto.style.display = filtrosColumna[col] ? 'inline-block' : 'none';
    }

    function ubicarPanel(panel, btnEl) {
      const rect = btnEl.getBoundingClientRect();
      panel.style.top = `${rect.bottom + 4}px`;
      panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 236))}px`;
      panel.style.display = 'block';
    }

    window.alternarFiltroColumna = function(col, btnEl) {
      const panel = document.getElementById('filtro-columna-panel');
      if (!panel) return;
      const mismoYAbierto = panel.style.display === 'block' && columnaFiltroAbierta === col;
      cerrarPanelesDesplegables();
      if (mismoYAbierto) return;

      columnaFiltroAbierta = col;
      document.getElementById('filtro-columna-titulo').textContent = 'Filtrar ' + COLUMNA[col].nombre;
      const lista = document.getElementById('filtro-columna-lista');

      if (ES_FECHA(col)) {
        const filtro = filtrosColumna[col]; // {desde, hasta} o undefined
        const desdeVal = filtro && filtro.desde ? formatearFechaInputLocal(filtro.desde) : '';
        const hastaVal = filtro && filtro.hasta ? formatearFechaInputLocal(filtro.hasta) : '';
        lista.innerHTML = `
            <div class="filtro-columna-fechas">
                <label>Desde:
                    <input type="date" id="filtro-columna-fecha-desde" value="${desdeVal}" onchange="window.actualizarFiltroColumnaFecha()">
                </label>
                <label>Hasta:
                    <input type="date" id="filtro-columna-fecha-hasta" value="${hastaVal}" onchange="window.actualizarFiltroColumnaFecha()">
                </label>
            </div>`;
        ubicarPanel(panel, btnEl);
        return;
      }

      lista.innerHTML = '<div class="filtro-columna-aviso">Cargando valores...</div>';
      ubicarPanel(panel, btnEl);

      obtenerValoresUnicos(col).then(valores => {
        if (columnaFiltroAbierta !== col) return;  // el usuario ya cerró o cambió de columna
        const seleccion = filtrosColumna[col]; // Set o undefined (= todos)
        const todosMarcados = !seleccion;
        lista.innerHTML = `
            <div class="filtro-columna-buscador">
                <input type="text" id="filtro-columna-buscador-input" placeholder="Buscar valor..."
                       oninput="window.filtrarValoresFiltroColumna(this.value)">
            </div>
            <div class="filtro-columna-opcion filtro-columna-opcion--todos">
                <input type="checkbox" id="fcv-todos" ${todosMarcados ? 'checked' : ''} onchange="window.alternarTodosFiltroColumna(this.checked)">
                <label for="fcv-todos"><strong>(Seleccionar todos)</strong></label>
            </div>
        ` + valores.map((v, i) => {
            const texto = etiquetaValor(col, v);
            const checked = todosMarcados || seleccion.has(v);
            return `<div class="filtro-columna-opcion fcv-fila" data-valor-texto="${tituloCelda(texto.toLowerCase())}">
                <input type="checkbox" class="fcv-valor" id="fcv-${i}" value="${tituloCelda(v)}" ${checked ? 'checked' : ''} onchange="window.actualizarFiltroColumna()">
                <label for="fcv-${i}">${escaparHtml(texto)}</label>
            </div>`;
        }).join('');
        const buscadorInput = document.getElementById('filtro-columna-buscador-input');
        if (buscadorInput) buscadorInput.focus();
      }).catch(() => {
        if (columnaFiltroAbierta === col) lista.innerHTML = '<div class="filtro-columna-aviso">No se pudieron cargar los valores. Inténtalo de nuevo.</div>';
      });
    };

    window.actualizarFiltroColumnaFecha = function() {
      const col = columnaFiltroAbierta;
      if (!col) return;
      const d = document.getElementById('filtro-columna-fecha-desde').value;
      const h = document.getElementById('filtro-columna-fecha-hasta').value;
      const desde = d ? new Date(d + 'T00:00:00').getTime() : null;
      const hasta = h ? new Date(h + 'T23:59:59').getTime() : null;
      if (!desde && !hasta) delete filtrosColumna[col];
      else filtrosColumna[col] = { desde, hasta };
      actualizarIconoFiltro(col);
      recargarRegistros();
    };

    window.filtrarValoresFiltroColumna = function(texto) {
      const t = texto.trim().toLowerCase();
      document.querySelectorAll('.fcv-fila').forEach(fila => {
        fila.style.display = (!t || fila.dataset.valorTexto.includes(t)) ? '' : 'none';
      });
    };

    window.alternarTodosFiltroColumna = function(marcarTodos) {
      document.querySelectorAll('.fcv-fila').forEach(fila => {
        if (fila.style.display === 'none') return; // no tocar lo oculto por la búsqueda
        const chk = fila.querySelector('.fcv-valor');
        if (chk) chk.checked = marcarTodos;
      });
      window.actualizarFiltroColumna();
    };

    window.actualizarFiltroColumna = function() {
      const col = columnaFiltroAbierta;
      if (!col) return;
      const checkboxes = Array.from(document.querySelectorAll('.fcv-valor'));
      const marcados = checkboxes.filter(c => c.checked).map(c => c.value);
      if (marcados.length === checkboxes.length) delete filtrosColumna[col];
      else filtrosColumna[col] = new Set(marcados);
      const todos = document.getElementById('fcv-todos');
      if (todos) todos.checked = marcados.length === checkboxes.length;
      actualizarIconoFiltro(col);
      recargarRegistros();
    };

    window.limpiarFiltroColumnaActual = function() {
      const col = columnaFiltroAbierta;
      if (!col) return;
      delete filtrosColumna[col];
      if (ES_FECHA(col)) {
        ['filtro-columna-fecha-desde', 'filtro-columna-fecha-hasta'].forEach(id => {
          const input = document.getElementById(id);
          if (input) input.value = '';
        });
      } else {
        document.querySelectorAll('.fcv-valor, #fcv-todos').forEach(c => c.checked = true);
        const buscadorInput = document.getElementById('filtro-columna-buscador-input');
        if (buscadorInput) buscadorInput.value = '';
        document.querySelectorAll('.fcv-fila').forEach(fila => fila.style.display = '');
      }
      actualizarIconoFiltro(col);
      recargarRegistros();
    };

    /* ============================================================
       8. PANEL DE DETALLE Y UBICACIÓN
    ============================================================ */

    window.abrirDetalleConsulta = function(idx) {
      const reg = registrosFiltrados[idx];
      if (reg) window.PanelGrabado.abrir(reg.grabado.id);
    };

    function mostrarMotivoBloqueo(celdaEl) {
      if (celdaEl.querySelector('.consulta-ubicacion__error')) return;
      const aviso = document.createElement('div');
      aviso.className = 'consulta-ubicacion__error';
      aviso.setAttribute('role', 'alert');
      // Texto corto para no estirar la fila; el motivo completo queda en el tooltip (title).
      aviso.textContent = 'Grabado inactivo';
      aviso.title = MOTIVO_GRABADO_INACTIVO;
      celdaEl.appendChild(aviso);
      setTimeout(() => aviso.remove(), 5000);
    }

    function editarUbicacion(celdaEl) {
      if (celdaEl.querySelector('input')) return;
      const reg = registrosFiltrados[Number(celdaEl.closest('.consulta-fila').dataset.idx)];
      if (!reg) return;
      if (grabadoInactivo(reg)) { mostrarMotivoBloqueo(celdaEl); return; }

      const grabado = reg.grabado;
      const contenidoOriginal = celdaEl.innerHTML;
      celdaEl.innerHTML = `<div>
          <input type="text" class="consulta-ubicacion__input" maxlength="20" autocomplete="off"
                 placeholder="Ej: GBB-2-1" aria-label="Nueva ubicación del grabado">
          <div class="consulta-ubicacion__error" role="alert"></div></div>`;
      const input = celdaEl.querySelector('input');
      input.value = grabado.ubicacion || '';
      input.focus();
      input.select();

      let guardando = false;
      const cerrar = () => { celdaEl.innerHTML = contenidoOriginal; };
      const mostrarError = (texto) => { celdaEl.querySelector('.consulta-ubicacion__error').textContent = texto; };

      input.addEventListener('click', (e) => e.stopPropagation());
      input.addEventListener('keydown', (e) => {
        e.stopPropagation();   // Enter no abre el panel de detalle
        if (e.key === 'Escape') { e.preventDefault(); cerrar(); celdaEl.focus(); }
        if (e.key !== 'Enter' || guardando) return;
        e.preventDefault();
        guardando = true;
        input.disabled = true;
        mostrarError('');
        fetch(`/grabados/api/grabado/${grabado.id}/ubicacion/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          body: JSON.stringify({ ubicacion: input.value, origen: 'CONSULTA' }),
        })
          .then(r => r.json())
          .then(res => {
            if (res.status !== 'ok') throw new Error(res.message || 'No se pudo guardar la ubicación.');
            // Un grabado tiene una fila por cada envío: se actualizan todas.
            DATOS_REGISTROS.forEach(r => {
              if (r.grabado.id === grabado.id) {
                r.grabado.ubicacion = res.ubicacion;
                r.grabado.actualizado_el = res.actualizado_el;
                r.ubicacion = res.ubicacion;
                r.modificado = res.actualizado_el;
              }
            });
            const idx = celdaEl.closest('.consulta-fila').dataset.idx;
            renderizarFilas();
            const nueva = document.querySelector(`.consulta-fila[data-idx="${idx}"] .consulta-ubicacion`);
            if (nueva) nueva.focus();
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

    const cuerpoTabla = document.getElementById('tabla-cuerpo');
    cuerpoTabla.addEventListener('click', (e) => {
      const etiqueta = e.target.closest('[data-etiqueta]');
      if (etiqueta) { window.Etiqueta.imprimirGrabado(etiqueta.dataset.etiqueta); return; }
      // La ubicación (doble clic = editar) y los botones de acción no abren el panel.
      if (e.target.closest('.consulta-ubicacion, .fila-acciones')) return;
      const fila = e.target.closest('.consulta-fila');
      if (fila) window.abrirDetalleConsulta(Number(fila.dataset.idx));
    });
    cuerpoTabla.addEventListener('dblclick', (e) => {
      const celdaEl = e.target.closest('.consulta-ubicacion');
      if (celdaEl) editarUbicacion(celdaEl);
    });
    cuerpoTabla.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      if (e.target.classList.contains('consulta-fila')) {
        e.preventDefault();
        window.abrirDetalleConsulta(Number(e.target.dataset.idx));
      } else if (e.target.classList.contains('consulta-ubicacion')) {
        e.preventDefault();
        editarUbicacion(e.target);
      }
    });

    // Desactivar / reactivar desde el panel cambia el estado de la fila y las tarjetas.
    document.addEventListener('panelgrabado:cambio', cargarDatosDesdeDB);

    /* ============================================================
       9. PANELES DESPLEGABLES, FECHA Y PAGINACIÓN
    ============================================================ */
    const PANELES_DESPLEGABLES = [
      { panelId: 'filtros-panel',      contenedorSelector: '.filtros-contenedor' },
      { panelId: 'exportar-panel',     contenedorSelector: '.exportar-contenedor' },
      { panelId: 'filtro-fecha-panel', contenedorSelector: '.filtro-fecha-contenedor' },
      { panelId: 'columnas-panel',     contenedorSelector: '.columnas-contenedor' }
    ];

    function cerrarPanelesDesplegables() {
      PANELES_DESPLEGABLES.forEach(({ panelId }) => {
        const panel = document.getElementById(panelId);
        if (panel) panel.style.display = 'none';
      });
      const panelColumna = document.getElementById('filtro-columna-panel');
      if (panelColumna) panelColumna.style.display = 'none';
      columnaFiltroAbierta = null;
    }

    document.addEventListener('click', function(evento) {
      const clicDentroDeAlgunPanel = PANELES_DESPLEGABLES.some(
        ({ contenedorSelector }) => evento.target.closest(contenedorSelector)
      ) || evento.target.closest('#filtro-columna-panel') || evento.target.closest('.boton-filtro-columna');
      if (!clicDentroDeAlgunPanel) cerrarPanelesDesplegables();
    });

    function alternarPanel(id) {
      const panel = document.getElementById(id);
      if (!panel) return;
      const yaAbierto = panel.style.display === 'block';
      cerrarPanelesDesplegables();
      panel.style.display = yaAbierto ? 'none' : 'block';
    }
    window.alternarFiltrosPanel = () => alternarPanel('filtros-panel');
    window.alternarFecha = () => alternarPanel('filtro-fecha-panel');
    window.alternarExportar = () => alternarPanel('exportar-panel');
    window.alternarColumnasPanel = () => alternarPanel('columnas-panel');

    window.limpiarTodosLosFiltros = function() {
      document.getElementById('filtro-estado').value = '';
      document.getElementById('filtro-proceso').value = '';
      recargarRegistros();
    };

    window.irAPagina = function(n) { paginaActual = n; actualizarUI(); };

    // Botón Fecha: fecha programada del envío (las filas de creación no la tienen y no pasan).
    window.aplicarFiltroDeFecha = function() {
      const d = document.getElementById('fecha-desde').value;
      const h = document.getElementById('fecha-hasta').value;
      fechaDesdeTs = d ? new Date(d + 'T00:00:00').getTime() : null;
      fechaHastaTs = h ? new Date(h + 'T23:59:59').getTime() : null;
      const etiqueta = document.getElementById('etiqueta-fecha');
      if (etiqueta) etiqueta.innerText = (d || h) ? 'Filtrado' : 'Fecha';
      recargarRegistros();
    };

    window.limpiarFechas = function() {
      document.getElementById('fecha-desde').value = '';
      document.getElementById('fecha-hasta').value = '';
      window.aplicarFiltroDeFecha();
    };

    /* ============================================================
       10. INICIALIZACIÓN
    ============================================================ */
    leerColumnasGuardadas();
    renderizarEncabezado();
    inicializarPanelColumnas();

    document.querySelectorAll('#tabla-registros thead th').forEach(th => {
      const col = th.dataset.col;
      if (!col || col === 'acciones') return;
      th.style.cursor = 'pointer';
      th.addEventListener('click', () => {
        if (columnaOrden === col) direccionOrden = direccionOrden === 'asc' ? 'desc' : 'asc';
        else { columnaOrden = col; direccionOrden = 'asc'; }
        ordenarYMostrar();
      });
    });

    document.querySelectorAll('.boton-filtro-columna').forEach(btn => {
      btn.addEventListener('click', ev => {
        ev.stopPropagation(); // no disparar el orden del th
        window.alternarFiltroColumna(btn.dataset.columna, btn);
      });
    });

    const buscador = document.getElementById('buscador-input');
    buscador.addEventListener('input', debounce(e => {
      palabrasBusqueda = e.target.value.trim().toLowerCase().split(/\s+/).filter(p => p);
      cargarRegistros();
    }, 300));

    aplicarVisibilidadColumnas();
    cargarDatosDesdeDB();
})();

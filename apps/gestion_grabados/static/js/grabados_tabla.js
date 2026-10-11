
/* Tabla de Consultas: filas de OrdenFabricacion ("Sistema anterior") y grabados
   creados en EIS sin migración ("Nuevo"), en una sola lista (columna Origen).
   - Filas "Nuevo": traen siempre reg.grabado; sin botones Editar/Eliminar (son de
     OrdenFabricacion) y el panel se abre solo con el grabado.
   - Clic (o Enter) en una fila: panel de detalle PanelGrabado. Si la OF tiene
     grabado enlazado (api_registros: reg.grabado) se abre el grabado con los
     datos de la orden; si no, solo la orden y su historial (api_historial).
   - Doble clic (o Enter) en Ubicación: edita la ubicación DEL GRABADO con
     api_grabado_ubicacion (origen CONSULTA, mismo servicio que el Inventario).
     Sin grabado enlazado la celda no se edita y muestra el motivo. */
(function() {
    'use strict';
    console.log("EIS: Script grabados_tabla.js cargado correctamente.");

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
       1. ESTADO DE LA APLICACIÓN
    ============================================================ */
    const REGISTROS_POR_PAGINA = 1000;

    let DATOS_REGISTROS    = [];
    let registrosFiltrados = [];
    let paginaActual       = 1;
    let palabrasBusqueda   = [];

    // Filtros por columna estilo Excel (todas las columnas de datos, salvo "acciones")
    const COLUMNAS_FILTRABLES = [
        'of', 'origen', 'ref', 'cliente', 'desc', 'estado', 'fecha', 'fecha_reg',
        'proceso', 'maquina', 'tipo', 'ubicacion', 'sobre', 'cantidad',
        'horas', 'responsables', 'peso_i', 'peso_f', 'perdida', 'temp',
        'rpm', 'tiempo_t', 'comp'
    ];  // 'grabado_mod' se ordena pero no se filtra
    const COLUMNAS_NUMERICAS = ['cantidad', 'horas', 'peso_i', 'peso_f', 'perdida', 'temp', 'rpm', 'tiempo_t', 'comp'];
    const COLUMNAS_FECHA = ['fecha', 'fecha_reg'];
    let filtrosColumna    = {}; // { estado: Set(['PENDIENTE','COMPLETADO']), ... } — sin entrada = sin filtro
    let columnaFiltroAbierta = null;

    let valoresColumna    = {}; // { cliente: ['A', 'B', ...] } — opciones del filtro, de todo el historial
    let totalServidor     = 0;  // filas que cumplen búsqueda + filtros (puede pasar del tope de 500)
    let consultaActual    = 0;  // descarta respuestas viejas si el usuario sigue filtrando

    // Sin filtro de fecha por defecto: el botón Fecha solo filtra si el usuario lo usa.
    let fechaDesdeTs       = null;
    let fechaHastaTs       = null;
    let columnaOrden       = null;  
    let direccionOrden     = 'asc'; 

    /* ============================================================
       2. CONSTANTES DE CONFIGURACIÓN
    ============================================================ */

    const NOMBRES_COLUMNAS = {
      of        : 'OF',
      origen    : 'Origen',
      ref       : 'OF Referencia',
      cliente   : 'Cliente',
      desc      : 'Descripción',
      estado    : 'Estado',
      fecha     : 'Fecha Prog.',
      fecha_reg : 'Fecha Registro',
      proceso   : 'Proceso',
      maquina   : 'Máquina',
      tipo      : 'Tipo de Grabado',
      ubicacion : 'Ubicación',
      sobre     : 'Sobre',
      papel     : 'Papel',
      cantidad  : 'Cantidad',
      horas     : 'Horas',
      responsables: 'Responsables',
      peso_i    : 'Peso Inicial',
      peso_f    : 'Peso Final',
      perdida   : 'Pérdida',
      temp      : 'Temperatura',
      rpm       : 'RPM',
      tiempo_t  : 'Tiempo T.',
      comp      : 'Compensación',
      grabado_mod: 'Últ. modif. grabado',
      acciones  : 'Acciones'
    };

    const CLASE_POR_ESTADO = {
      'PENDIENTE'  : 'estado--pendiente',
      'EN_PROCESO' : 'estado--en-proceso',
      'EN_MAQUINA' : 'estado--en-proceso',
      'COMPLETADO' : 'estado--completado',
      'CANCELADO'  : 'estado--cancelado',
      'REVISION'   : 'estado--en-revision',
      'REPETIR'    : 'estado--cancelado',
      // Estados de Grabado (filas "Nuevo")
      'EN_FABRICACION': 'estado--en-proceso',
      'PENDIENTE_K1'  : 'estado--en-revision',
      'APROBADO'      : 'estado--completado',
      'INACTIVO'      : 'estado--inactivo'
    };
    const ORIGEN_NUEVO = 'Nuevo';

    const SVG_CANDADO = `<svg viewBox="0 0 24 24" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>`;
    const SVG_PIN = `<svg viewBox="0 0 24 24" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`;

    /* ============================================================
       3. UTILIDADES
    ============================================================ */

    function parsearFecha(str) {
      if (!str || str === '—') return null;
      const partes = str.split('/');
      return partes.length === 3 ? new Date(partes[2], partes[1] - 1, partes[0]).getTime() : null;
    }

    // 'dd/mm/aaaa hh:mm' (Últ. modif. grabado)
    function parsearFechaHora(str) {
      const m = /^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2})$/.exec(str || '');
      return m ? new Date(m[3], m[2] - 1, m[1], m[4], m[5]).getTime() : null;
    }

    function escaparHtml(valor) {
      const div = document.createElement('div');
      div.textContent = valor === null || valor === undefined || valor === '' ? '—' : String(valor);
      return div.innerHTML;
    }

    // Texto completo para el title de una celda cortada con "..." (.celda-texto).
    function tituloCelda(valor) {
      return escaparHtml(valor).replace(/"/g, '&quot;');
    }

    function resaltarTexto(texto, palabras) {
      const str = String(texto || '—');
      if (!palabras || palabras.length === 0) return str;
      let res = str;
      palabras.forEach(p => {
        const regex = new RegExp(`(${p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        res = res.replace(regex, '<mark class="texto-resaltado">$1</mark>');
      });
      return res;
    }

    /* ============================================================
       4. CARGA Y FILTROS
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

    // Filtros que se mandan al servidor (formato de selectors.consulta_ordenes): así la
    // búsqueda y los filtros miran todo el historial, no solo las filas cargadas.
    function filtrosParaServidor() {
      const filtros = [];
      const estado = document.getElementById('filtro-estado') ? document.getElementById('filtro-estado').value : '';
      const proceso = document.getElementById('filtro-proceso') ? document.getElementById('filtro-proceso').value : '';
      if (estado) filtros.push({ col: 'estado', in: [estado] });
      if (proceso) filtros.push({ col: 'proceso', in: [proceso] });
      if (fechaDesdeTs || fechaHastaTs) {
        filtros.push({ col: 'fecha',
                       desde: fechaDesdeTs ? formatearFechaInputLocal(fechaDesdeTs) : null,
                       hasta: fechaHastaTs ? formatearFechaInputLocal(fechaHastaTs) : null });
      }
      for (const col of COLUMNAS_FILTRABLES) {
        const filtro = filtrosColumna[col];
        if (!filtro) continue;
        if (COLUMNAS_FECHA.includes(col)) {
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
          aplicarFiltrosCombinados();
        })
        .catch(err => console.error("Error al cargar registros:", err));
    }
    const recargarRegistros = debounce(cargarRegistros, 250);
    window.recargarRegistros = recargarRegistros;

    function cargarDatosDesdeDB() {
      cargarKPIs();
      cargarRegistros();
    }

    function aplicarFiltrosCombinados() {
      const filtroEstado = document.getElementById('filtro-estado') ? document.getElementById('filtro-estado').value : '';
      const filtroProceso = document.getElementById('filtro-proceso') ? document.getElementById('filtro-proceso').value : '';

      let filtrados = DATOS_REGISTROS.filter(reg => {
        if (fechaDesdeTs || fechaHastaTs) {
          const ts = parsearFecha(reg.fecha);
          if (!ts || (fechaDesdeTs && ts < fechaDesdeTs) || (fechaHastaTs && ts > fechaHastaTs)) return false;
        }
        if (filtroEstado && reg.estado !== filtroEstado) return false;
        if (filtroProceso && reg.proceso !== filtroProceso) return false;
        for (const col of COLUMNAS_FILTRABLES) {
          const filtro = filtrosColumna[col];
          if (!filtro) continue;
          if (COLUMNAS_FECHA.includes(col)) {
            const ts = parsearFecha(reg[col]);
            if (!ts || (filtro.desde && ts < filtro.desde) || (filtro.hasta && ts > filtro.hasta)) return false;
          } else if (!filtro.has(normalizarValorColumna(reg[col]))) {
            return false;
          }
        }
        return true;
      });

      if (columnaOrden) {
        filtrados.sort((a, b) => {
          let valA = a[columnaOrden], valB = b[columnaOrden];
          if (valA === null || valA === undefined || valA === '—') valA = '';
          if (valB === null || valB === undefined || valB === '—') valB = '';
          if (columnaOrden === 'fecha' || columnaOrden === 'fecha_reg') {
            valA = parsearFecha(valA) || 0; valB = parsearFecha(valB) || 0;
          } else if (columnaOrden === 'grabado_mod') {
            valA = parsearFechaHora(valA) || 0; valB = parsearFechaHora(valB) || 0;
          } else if (['peso_i', 'peso_f', 'perdida', 'temp', 'rpm', 'cantidad', 'horas'].includes(columnaOrden)) {
            valA = parseFloat(valA) || 0; valB = parseFloat(valB) || 0;
          } else {
            valA = String(valA).toLowerCase(); valB = String(valB).toLowerCase();
          }
          if (valA < valB) return direccionOrden === 'asc' ? -1 : 1;
          if (valA > valB) return direccionOrden === 'asc' ? 1 : -1;
          return 0;
        });
      }  // sin orden elegido queda el del servidor: fecha de registro más reciente primero

      registrosFiltrados = filtrados;
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

    function renderizarFilas() {
      const inicio = (paginaActual - 1) * REGISTROS_POR_PAGINA;
      const pagina = registrosFiltrados.slice(inicio, inicio + REGISTROS_POR_PAGINA);
      const tbody  = document.getElementById('tabla-cuerpo');
      if (!tbody) return;
      
      if (pagina.length === 0) {
        tbody.innerHTML = `<tr><td colspan="25" class="tabla-sin-resultados">No se encontraron registros.</td></tr>`;
        return;
      }

      const isAdmin = window.USER_IS_ADMIN === true;

      tbody.innerHTML = pagina.map((reg, idx) => {
        const globalIdx = inicio + idx;
        let htmlAcciones = `<div class="fila-acciones">
            <button class="boton-accion boton-accion--ver" onclick="window.abrirDetalleConsulta(${globalIdx})" title="Ver detalle">
                <svg viewBox="0 0 24 24" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
            ${reg.grabado
              ? `<button type="button" class="boton-accion boton-accion--etiqueta" data-etiqueta="${reg.grabado.id}"
                         title="Imprimir etiqueta del grabado ${escaparHtml(reg.grabado.of_origen)} ${escaparHtml(reg.grabado.proceso)}">${window.Etiqueta.BOTON_SVG}</button>`
              : `<button type="button" class="boton-accion boton-accion--etiqueta" disabled
                         title="Sin grabado en EIS: no hay etiqueta que imprimir">${window.Etiqueta.BOTON_SVG}</button>`}`;
        if (isAdmin && reg.origen !== ORIGEN_NUEVO) {
          htmlAcciones += `
            <button class="boton-accion boton-accion--editar" onclick="window.editarRegistro(${globalIdx})" title="Editar en Admin">
                <svg viewBox="0 0 24 24" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            </button>
            <button class="boton-accion boton-accion--eliminar" onclick="window.eliminarRegistro('${reg.of}', '${reg.proceso}')" title="Eliminar">
                <svg viewBox="0 0 24 24" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
            </button>`;
        }
        htmlAcciones += `</div>`;

        return `
        <tr class="consulta-fila" tabindex="0" data-idx="${globalIdx}">
          <td data-col="of"><span class="celda-of-enlace">${resaltarTexto(reg.of, palabrasBusqueda)}</span></td>
          <td data-col="origen"><span class="consulta-origen ${reg.origen === ORIGEN_NUEVO ? 'consulta-origen--nuevo' : ''}">${escaparHtml(reg.origen)}</span></td>
          <td data-col="ref"><span class="celda-of-referencia">${resaltarTexto(reg.ref, palabrasBusqueda)}</span></td>
          <td data-col="cliente"><span class="celda-cliente celda-texto" title="${tituloCelda(reg.cliente)}">${resaltarTexto(reg.cliente, palabrasBusqueda)}</span></td>
          <td data-col="desc"><span class="celda-descripcion celda-texto" title="${tituloCelda(reg.desc)}">${resaltarTexto(reg.desc, palabrasBusqueda)}</span></td>
          <td data-col="estado"><span class="celda-estado ${CLASE_POR_ESTADO[reg.estado] || 'estado--pendiente'}">${reg.estado}</span></td>
          <td data-col="fecha">${resaltarTexto(reg.fecha, palabrasBusqueda)}</td>
          <td data-col="fecha_reg">${resaltarTexto(reg.fecha_reg, palabrasBusqueda)}</td>
          <td data-col="proceso">${resaltarTexto(reg.proceso, palabrasBusqueda)}</td>
          <td data-col="maquina"><span class="celda-maquina" title="${tituloCelda(reg.maquina)}"><span class="celda-texto">${resaltarTexto(reg.maquina, palabrasBusqueda)}</span></span></td>
          <td data-col="tipo">${resaltarTexto(reg.tipo, palabrasBusqueda)}</td>
          ${htmlCeldaUbicacion(reg)}
          <td data-col="sobre"><span class="celda-ubicacion" title="${tituloCelda(reg.sobre)}">${SVG_PIN}<span class="celda-texto">${resaltarTexto(reg.sobre, palabrasBusqueda)}</span></span></td>
          <td data-col="cantidad">${resaltarTexto(reg.cantidad, palabrasBusqueda)}</td>
          <td data-col="horas">${resaltarTexto(reg.horas, palabrasBusqueda)}</td>
          <td data-col="responsables"><span class="celda-texto" title="${tituloCelda(reg.responsables)}">${resaltarTexto(reg.responsables, palabrasBusqueda)}</span></td>
          <td data-col="peso_i">${resaltarTexto(reg.peso_i, palabrasBusqueda)}</td>
          <td data-col="peso_f">${resaltarTexto(reg.peso_f, palabrasBusqueda)}</td>
          <td data-col="perdida">${resaltarTexto(reg.perdida, palabrasBusqueda)}</td>
          <td data-col="temp">${resaltarTexto(reg.temp, palabrasBusqueda)}°C</td>
          <td data-col="rpm">${resaltarTexto(reg.rpm, palabrasBusqueda)}</td>
          <td data-col="tiempo_t">${resaltarTexto(reg.tiempo_t, palabrasBusqueda)}</td>
          <td data-col="comp">${resaltarTexto(reg.comp, palabrasBusqueda)}</td>
          <td data-col="grabado_mod" class="consulta-modificado">${escaparHtml(reg.grabado_mod)}</td>
          <td data-col="acciones">${htmlAcciones}</td>
        </tr>
      `}).join('');
      
      // Aplicar visibilidad actual de columnas tras renderizar
      aplicarVisibilidadColumnas();
    }

    const MOTIVO_GRABADO_INACTIVO = 'Grabado inactivo: reactívalo desde su panel para cambiar la ubicación.';

    function grabadoInactivo(reg) {
      return Boolean(reg.grabado && reg.grabado.estado === 'INACTIVO');
    }

    function htmlCeldaUbicacion(reg) {
      if (grabadoInactivo(reg)) {
        return `<td data-col="ubicacion" class="consulta-ubicacion consulta-ubicacion--bloqueada" tabindex="0"
                    title="${escaparHtml(MOTIVO_GRABADO_INACTIVO)}">
                  <span class="celda-ubicacion">${SVG_CANDADO}${resaltarTexto(reg.ubicacion, palabrasBusqueda)}</span></td>`;
      }
      if (reg.grabado) {
        return `<td data-col="ubicacion" class="consulta-ubicacion" tabindex="0"
                    title="Ubicación del grabado ${escaparHtml(reg.grabado.of_origen)} ${escaparHtml(reg.grabado.proceso)}. Doble clic para editarla">
                  <span class="celda-ubicacion">${SVG_PIN}${resaltarTexto(reg.ubicacion, palabrasBusqueda)}</span></td>`;
      }
      return `<td data-col="ubicacion" class="consulta-ubicacion consulta-ubicacion--bloqueada" tabindex="0"
                  title="${escaparHtml(reg.motivo_sin_grabado)}">
                <span class="celda-ubicacion">${SVG_CANDADO}${resaltarTexto(reg.ubicacion, palabrasBusqueda)}</span></td>`;
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
       6. GESTIÓN DE COLUMNAS
    ============================================================ */

    function inicializarPanelColumnas() {
        const lista = document.getElementById('columnas-lista');
        if (!lista) return;

        let html = '';
        Object.keys(NOMBRES_COLUMNAS).forEach(id => {
            const esFija = (id === 'of' || id === 'acciones');
            html += `
                <div class="columna-opcion ${esFija ? 'columna-opcion--fija' : ''}">
                    <input type="checkbox" id="chk-col-${id}" ${esFija ? 'disabled checked' : 'checked'} 
                           onchange="window.toggleColumna('${id}')">
                    <label for="chk-col-${id}">${NOMBRES_COLUMNAS[id]}</label>
                    ${esFija ? '<span class="columna-opcion__fija">fijo</span>' : ''}
                </div>
            `;
        });
        lista.innerHTML = html;
        
        // Cargar estado de localStorage si existiera
        const ocultas = JSON.parse(localStorage.getItem('columnas_ocultas_grabados') || '[]');
        ocultas.forEach(id => {
            const chk = document.getElementById(`chk-col-${id}`);
            if (chk) {
                chk.checked = false;
                window.toggleColumna(id, false);
            }
        });
    }

    window.toggleColumna = function(id, guardar = true) {
        const chk = document.getElementById(`chk-col-${id}`);
        const visible = chk ? chk.checked : true;
        
        // Buscar todos los elementos (th y td) con ese data-col
        const elementos = document.querySelectorAll(`[data-col="${id}"]`);
        elementos.forEach(el => {
            el.style.display = visible ? '' : 'none';
        });

        if (guardar) {
            actualizarMemoriaColumnas();
        }
    };

    function aplicarVisibilidadColumnas() {
        Object.keys(NOMBRES_COLUMNAS).forEach(id => {
            const chk = document.getElementById(`chk-col-${id}`);
            if (chk && !chk.checked) {
                const elementos = document.querySelectorAll(`[data-col="${id}"]`);
                elementos.forEach(el => el.style.display = 'none');
            }
        });
    }

    function actualizarMemoriaColumnas() {
        const ocultas = [];
        Object.keys(NOMBRES_COLUMNAS).forEach(id => {
            const chk = document.getElementById(`chk-col-${id}`);
            if (chk && !chk.checked) ocultas.push(id);
        });
        localStorage.setItem('columnas_ocultas_grabados', JSON.stringify(ocultas));
    }

    window.restablecerColumnas = function() {
        Object.keys(NOMBRES_COLUMNAS).forEach(id => {
            const chk = document.getElementById(`chk-col-${id}`);
            if (chk && !chk.disabled) {
                chk.checked = true;
                window.toggleColumna(id, false);
            }
        });
        localStorage.removeItem('columnas_ocultas_grabados');
    };

    /* ============================================================
       6b. FILTROS POR COLUMNA (estilo Excel)
    ============================================================ */

    function normalizarValorColumna(v) {
        return (v === null || v === undefined || v === '' || v === '—') ? '—' : String(v);
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
                const valores = ordenarValores(col, Array.from(new Set(res.valores.map(normalizarValorColumna))));
                // Un valor marcado que ya no sale (ej. cambió la búsqueda) se sigue mostrando.
                if (filtrosColumna[col]) filtrosColumna[col].forEach(v => { if (!valores.includes(v)) valores.push(v); });
                valoresColumna[col] = valores;
                return valores;
            });
    }

    function ordenarValores(col, valores) {
        if (COLUMNAS_NUMERICAS.includes(col)) {
            valores.sort((a, b) => (parseFloat(a) || 0) - (parseFloat(b) || 0));
        } else {
            valores.sort((a, b) => a.localeCompare(b, 'es'));
        }
        return valores;
    }

    function formatearFechaInputLocal(ts) {
        const d = new Date(ts);
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
    }

    function actualizarIconoFiltro(col) {
        const btn = document.querySelector(`.boton-filtro-columna[data-columna="${col}"]`);
        if (btn) btn.classList.toggle('boton-filtro-columna--activo', !!filtrosColumna[col]);

        const punto = document.getElementById(`indicador-filtro-${col}`);
        if (punto) punto.style.display = filtrosColumna[col] ? 'inline-block' : 'none';
    }

    window.alternarFiltroColumna = function(col, btnEl) {
        const panel = document.getElementById('filtro-columna-panel');
        if (!panel) return;
        const mismoYAbierto = panel.style.display === 'block' && columnaFiltroAbierta === col;
        cerrarPanelesDesplegables();
        if (mismoYAbierto) return;

        columnaFiltroAbierta = col;
        document.getElementById('filtro-columna-titulo').textContent = 'Filtrar ' + NOMBRES_COLUMNAS[col];

        const lista = document.getElementById('filtro-columna-lista');

        if (COLUMNAS_FECHA.includes(col)) {
            const filtro = filtrosColumna[col]; // {desde, hasta} o undefined
            const desdeVal = filtro && filtro.desde ? formatearFechaInputLocal(filtro.desde) : '';
            const hastaVal = filtro && filtro.hasta ? formatearFechaInputLocal(filtro.hasta) : '';
            lista.innerHTML = `
                <div style="padding:12px 14px; display:flex; flex-direction:column; gap:10px;">
                    <label style="font-size:11px; font-weight:600; color:var(--color-texto-suave);">Desde:
                        <input type="date" id="filtro-columna-fecha-desde" value="${desdeVal}"
                               style="display:block; width:100%; margin-top:4px; padding:5px 8px; border:1px solid var(--color-borde); border-radius:6px; font-size:12px; box-sizing:border-box;"
                               onchange="window.actualizarFiltroColumnaFecha()">
                    </label>
                    <label style="font-size:11px; font-weight:600; color:var(--color-texto-suave);">Hasta:
                        <input type="date" id="filtro-columna-fecha-hasta" value="${hastaVal}"
                               style="display:block; width:100%; margin-top:4px; padding:5px 8px; border:1px solid var(--color-borde); border-radius:6px; font-size:12px; box-sizing:border-box;"
                               onchange="window.actualizarFiltroColumnaFecha()">
                    </label>
                </div>
            `;
            const rectFecha = btnEl.getBoundingClientRect();
            panel.style.top = `${rectFecha.bottom + 4}px`;
            panel.style.left = `${Math.min(rectFecha.left, window.innerWidth - 236)}px`;
            panel.style.display = 'block';
            return;
        }

        lista.innerHTML = '<div style="padding:12px 14px; font-size:12px; color:var(--color-texto-suave);">Cargando valores...</div>';
        const rect = btnEl.getBoundingClientRect();
        panel.style.top = `${rect.bottom + 4}px`;
        panel.style.left = `${Math.min(rect.left, window.innerWidth - 236)}px`;
        panel.style.display = 'block';

        obtenerValoresUnicos(col).then(valores => {
            if (columnaFiltroAbierta !== col) return;  // el usuario ya cerró o cambió de columna
            const seleccion = filtrosColumna[col]; // Set o undefined (= todos)
            const todosMarcados = !seleccion;

            lista.innerHTML = `
                <div class="filtro-columna-buscador" style="position:sticky; top:0; background:#fff; padding:6px 14px; border-bottom:1px solid var(--color-borde);">
                    <input type="text" id="filtro-columna-buscador-input" placeholder="Buscar valor..."
                           style="width:100%; box-sizing:border-box; padding:5px 8px; border:1px solid var(--color-borde); border-radius:6px; font-size:12px;"
                           oninput="window.filtrarValoresFiltroColumna(this.value)">
                </div>
                <div class="filtro-columna-opcion filtro-columna-opcion--todos">
                    <input type="checkbox" id="fcv-todos" ${todosMarcados ? 'checked' : ''} onchange="window.alternarTodosFiltroColumna(this.checked)">
                    <label for="fcv-todos"><strong>(Seleccionar todos)</strong></label>
                </div>
            ` + valores.map((v, i) => {
                const checked = todosMarcados || seleccion.has(v);
                return `<div class="filtro-columna-opcion fcv-fila" data-valor-texto="${v.toLowerCase().replace(/"/g, '&quot;')}">
                    <input type="checkbox" class="fcv-valor" id="fcv-${i}" value="${v.replace(/"/g, '&quot;')}" ${checked ? 'checked' : ''} onchange="window.actualizarFiltroColumna()">
                    <label for="fcv-${i}">${escaparHtml(v)}</label>
                </div>`;
            }).join('');

            const buscadorInput = document.getElementById('filtro-columna-buscador-input');
            if (buscadorInput) buscadorInput.focus();
        }).catch(() => {
            if (columnaFiltroAbierta === col) lista.innerHTML = '<div style="padding:12px 14px; font-size:12px;">No se pudieron cargar los valores. Inténtalo de nuevo.</div>';
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
        if (COLUMNAS_FECHA.includes(col)) {
            const desdeInput = document.getElementById('filtro-columna-fecha-desde');
            const hastaInput = document.getElementById('filtro-columna-fecha-hasta');
            if (desdeInput) desdeInput.value = '';
            if (hastaInput) hastaInput.value = '';
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
       7. FUNCIONES GLOBALES (Exportadas)
    ============================================================ */

    window.abrirDetalleConsulta = function(idx) {
        const reg = registrosFiltrados[idx];
        if (!reg) return;
        if (reg.origen === ORIGEN_NUEVO) window.PanelGrabado.abrir(reg.grabado.id);
        else if (reg.grabado) window.PanelGrabado.abrir(reg.grabado.id, { orden: reg });
        else window.PanelGrabado.abrirOrden(reg);
    };

    /* --- Editar ubicación del grabado (doble clic o Enter en la celda) --- */
    function mostrarMotivoBloqueo(celda, reg) {
        if (celda.querySelector('.consulta-ubicacion__error')) return;
        const aviso = document.createElement('div');
        aviso.className = 'consulta-ubicacion__error';
        aviso.setAttribute('role', 'alert');
        // Texto corto para no estirar la fila; el motivo completo queda en el tooltip (title).
        const inactivo = grabadoInactivo(reg);
        aviso.textContent = inactivo ? 'Grabado inactivo' : 'Sin grabado en EIS';
        aviso.title = inactivo ? MOTIVO_GRABADO_INACTIVO : reg.motivo_sin_grabado;
        celda.appendChild(aviso);
        setTimeout(() => aviso.remove(), 5000);
    }

    function editarUbicacion(celda) {
        if (celda.querySelector('input')) return;
        const reg = registrosFiltrados[Number(celda.closest('.consulta-fila').dataset.idx)];
        if (!reg) return;
        if (!reg.grabado || grabadoInactivo(reg)) { mostrarMotivoBloqueo(celda, reg); return; }

        const grabado = reg.grabado;
        const contenidoOriginal = celda.innerHTML;
        celda.innerHTML = `<div>
            <input type="text" class="consulta-ubicacion__input" maxlength="20" autocomplete="off"
                   placeholder="Ej: GBB-2-1" aria-label="Nueva ubicación del grabado">
            <div class="consulta-ubicacion__error" role="alert"></div></div>`;
        const input = celda.querySelector('input');
        input.value = grabado.ubicacion || '';
        input.focus();
        input.select();

        let guardando = false;
        const cerrar = () => { celda.innerHTML = contenidoOriginal; };
        const mostrarError = (texto) => { celda.querySelector('.consulta-ubicacion__error').textContent = texto; };

        input.addEventListener('click', (e) => e.stopPropagation());
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
                body: JSON.stringify({ ubicacion: input.value, origen: 'CONSULTA' }),
            })
                .then(r => r.json())
                .then(res => {
                    if (res.status !== 'ok') throw new Error(res.message || 'No se pudo guardar la ubicación.');
                    // Varias OF pueden usar el mismo grabado: se actualizan todas sus filas.
                    DATOS_REGISTROS.forEach(r => {
                        if (r.grabado && r.grabado.id === grabado.id) {
                            r.grabado.ubicacion = res.ubicacion;
                            r.grabado.actualizado_el = res.actualizado_el;
                            r.ubicacion = res.ubicacion;
                            r.grabado_mod = res.actualizado_el;
                        }
                    });
                    const idx = celda.closest('.consulta-fila').dataset.idx;
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
    if (cuerpoTabla) {
        cuerpoTabla.addEventListener('click', (e) => {
            const etiqueta = e.target.closest('[data-etiqueta]');
            if (etiqueta) { window.Etiqueta.imprimirGrabado(etiqueta.dataset.etiqueta); return; }
            // La ubicación (doble clic = editar) y los botones de acción no abren el panel.
            if (e.target.closest('.consulta-ubicacion, .fila-acciones')) return;
            const fila = e.target.closest('.consulta-fila');
            if (fila) window.abrirDetalleConsulta(Number(fila.dataset.idx));
        });
        cuerpoTabla.addEventListener('dblclick', (e) => {
            const celda = e.target.closest('.consulta-ubicacion');
            if (celda) editarUbicacion(celda);
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
    }

    // Desactivar / reactivar desde el panel cambia el estado de la fila y las tarjetas.
    document.addEventListener('panelgrabado:cambio', cargarDatosDesdeDB);

    /* --- Cierre de paneles desplegables (clic afuera / exclusión mutua) --- */
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

    window.alternarFiltrosPanel = function() {
        const panel = document.getElementById('filtros-panel');
        if (!panel) return;
        const yaAbierto = panel.style.display === 'block';
        cerrarPanelesDesplegables();
        panel.style.display = yaAbierto ? 'none' : 'block';
    };

    window.limpiarTodosLosFiltros = function() {
        const e = document.getElementById('filtro-estado'), p = document.getElementById('filtro-proceso');
        if (e) e.value = ''; if (p) p.value = '';
        recargarRegistros();
    };

    window.irAPagina = function(n) { paginaActual = n; actualizarUI(); };

    window.editarRegistro = function(idx) {
        const reg = registrosFiltrados[idx];
        window.open(`/crdadmin/gestion_grabados/ordenfabricacion/?q=${reg.of}`, '_blank');
    };

    window.eliminarRegistro = function(of_num, proceso) {
        if (!confirm(`¿Eliminar OF ${of_num} (${proceso})?`)) return;
        fetch('/grabados/api/eliminar/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({ of: of_num, proceso: proceso })
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'ok') { alert("Eliminado."); cargarDatosDesdeDB(); }
            else alert("Error: " + data.message);
        });
    };

    /* --- Funciones de Filtrado por Fecha --- */
    window.alternarFecha = function() {
        const panel = document.getElementById('filtro-fecha-panel');
        if (!panel) return;
        const yaAbierto = panel.style.display === 'block';
        cerrarPanelesDesplegables();
        panel.style.display = yaAbierto ? 'none' : 'block';
    };

    window.aplicarFiltroDeFecha = function() {
        const d = document.getElementById('fecha-desde').value;
        const h = document.getElementById('fecha-hasta').value;
        // Normalizamos a inicio y fin de día para que el rango sea inclusivo
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

    /* --- Funciones de UI adicionales --- */
    window.alternarExportar = function() {
        const panel = document.getElementById('exportar-panel');
        if (!panel) return;
        const yaAbierto = panel.style.display === 'block';
        cerrarPanelesDesplegables();
        panel.style.display = yaAbierto ? 'none' : 'block';
    };

    window.alternarColumnasPanel = function() {
        const panel = document.getElementById('columnas-panel');
        if (!panel) return;
        const yaAbierto = panel.style.display === 'block';
        cerrarPanelesDesplegables();
        panel.style.display = yaAbierto ? 'none' : 'block';
    };

    /* ============================================================
       8. INICIALIZACIÓN
    ============================================================ */
    
    document.querySelectorAll('#tabla-registros thead th').forEach(th => {
        const col = th.dataset.col;
        if (col && col !== 'acciones') {
            th.style.cursor = 'pointer';
            th.addEventListener('click', () => {
                if (columnaOrden === col) direccionOrden = direccionOrden === 'asc' ? 'desc' : 'asc';
                else { columnaOrden = col; direccionOrden = 'asc'; }
                aplicarFiltrosCombinados();
            });
        }
    });

    const buscador = document.getElementById('buscador-input');
    if (buscador) {
        buscador.addEventListener('input', debounce(e => {
            const texto = e.target.value.trim();
            palabrasBusqueda = texto.toLowerCase().split(/\s+/).filter(p => p);
            cargarRegistros();
        }, 300));
    }

    document.querySelectorAll('.boton-filtro-columna').forEach(btn => {
        btn.addEventListener('click', ev => {
            ev.stopPropagation(); // no disparar el orden del th
            window.alternarFiltroColumna(btn.dataset.columna, btn);
        });
    });

    inicializarPanelColumnas();
    cargarDatosDesdeDB();
})();

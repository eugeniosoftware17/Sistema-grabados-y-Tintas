
(function() {
    'use strict';
    console.log("EIS: Script grabados_tabla.js cargado correctamente.");

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    /* ============================================================
       1. ESTADO DE LA APLICACIÓN
    ============================================================ */
    const REGISTROS_POR_PAGINA = 1000;

    let DATOS_REGISTROS    = []; 
    let registrosFiltrados = []; 
    let paginaActual       = 1;  
    let palabrasBusqueda   = []; 

    // Configurar filtro de los últimos 3 meses por defecto
    const fechaInicioDefault = new Date();
    fechaInicioDefault.setMonth(fechaInicioDefault.getMonth() - 3);
    fechaInicioDefault.setHours(0, 0, 0, 0);

    let fechaDesdeTs       = fechaInicioDefault.getTime(); 
    let fechaHastaTs       = null; 
    let columnaOrden       = null;  
    let direccionOrden     = 'asc'; 

    /* ============================================================
       2. CONSTANTES DE CONFIGURACIÓN
    ============================================================ */

    const NOMBRES_COLUMNAS = {
      of        : 'OF',
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
      acciones  : 'Acciones'
    };

    const CLASE_POR_ESTADO = {
      'PENDIENTE'  : 'estado--pendiente',
      'EN_PROCESO' : 'estado--en-proceso',
      'EN_MAQUINA' : 'estado--en-proceso',
      'COMPLETADO' : 'estado--completado',
      'CANCELADO'  : 'estado--cancelado',
      'REVISION'   : 'estado--en-revision',
      'REPETIR'    : 'estado--cancelado'
    };

    const SVG_PIN = `<svg viewBox="0 0 24 24" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`;

    /* ============================================================
       3. UTILIDADES
    ============================================================ */

    function parsearFecha(str) {
      if (!str || str === '—') return null;
      const partes = str.split('/');
      return partes.length === 3 ? new Date(partes[2], partes[1] - 1, partes[0]).getTime() : null;
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

    function cargarDatosDesdeDB() {
      // Cargar KPIs
      fetch('/grabados/api/kpis/')
        .then(res => res.json())
        .then(kpis => {
            document.getElementById('kpi-maquina').innerText = kpis.en_maquina || 0;
            document.getElementById('kpi-hoy').innerText = kpis.completados_hoy || 0;
            document.getElementById('kpi-repetir').innerText = kpis.para_repetir || 0;
            document.getElementById('kpi-pendientes').innerText = kpis.pendientes || 0;
        });

      fetch('/grabados/api/registros/')
        .then(res => res.json())
        .then(data => {
          DATOS_REGISTROS = data;
          aplicarFiltrosCombinados();
        })
        .catch(err => console.error("Error al cargar registros:", err));
    }

    function aplicarFiltrosCombinados() {
      const filtroEstado = document.getElementById('filtro-estado') ? document.getElementById('filtro-estado').value : '';
      const filtroProceso = document.getElementById('filtro-proceso') ? document.getElementById('filtro-proceso').value : '';

      let filtrados = DATOS_REGISTROS.filter(reg => {
        if (palabrasBusqueda.length > 0) {
          const pool = Object.values(reg).join(' ').toLowerCase();
          if (!palabrasBusqueda.every(p => pool.includes(p))) return false;
        }
        if (fechaDesdeTs || fechaHastaTs) {
          const ts = parsearFecha(reg.fecha);
          if (!ts || (fechaDesdeTs && ts < fechaDesdeTs) || (fechaHastaTs && ts > fechaHastaTs)) return false;
        }
        if (filtroEstado && reg.estado !== filtroEstado) return false;
        if (filtroProceso && reg.proceso !== filtroProceso) return false;
        return true;
      });

      if (columnaOrden) {
        filtrados.sort((a, b) => {
          let valA = a[columnaOrden], valB = b[columnaOrden];
          if (valA === null || valA === undefined || valA === '—') valA = '';
          if (valB === null || valB === undefined || valB === '—') valB = '';
          if (columnaOrden === 'fecha' || columnaOrden === 'fecha_reg') {
            valA = parsearFecha(valA) || 0; valB = parsearFecha(valB) || 0;
          } else if (['peso_i', 'peso_f', 'perdida', 'temp', 'rpm', 'cantidad', 'horas'].includes(columnaOrden)) {
            valA = parseFloat(valA) || 0; valB = parseFloat(valB) || 0;
          } else {
            valA = String(valA).toLowerCase(); valB = String(valB).toLowerCase();
          }
          if (valA < valB) return direccionOrden === 'asc' ? -1 : 1;
          if (valA > valB) return direccionOrden === 'asc' ? 1 : -1;
          return 0;
        });
      } else {
        filtrados.sort((a, b) => (parsearFecha(b.fecha) || 0) - (parsearFecha(a.fecha) || 0));
      }

      registrosFiltrados = filtrados;
      paginaActual = 1;
      actualizarUI();
    }

    function actualizarUI() {
      renderizarFilas();
      renderizarPaginacion(registrosFiltrados.length);
      const info = document.getElementById('pie-info');
      if (info) {
        const inicio = registrosFiltrados.length === 0 ? 0 : (paginaActual - 1) * REGISTROS_POR_PAGINA + 1;
        const fin = Math.min(paginaActual * REGISTROS_POR_PAGINA, registrosFiltrados.length);
        info.innerHTML = `Mostrando <strong>${inicio}–${fin}</strong> de <strong>${registrosFiltrados.length}</strong> registros`;
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
        tbody.innerHTML = `<tr><td colspan="23" class="tabla-sin-resultados">No se encontraron registros.</td></tr>`;
        return;
      }

      const isAdmin = window.USER_IS_ADMIN === true;

      tbody.innerHTML = pagina.map((reg, idx) => {
        const globalIdx = inicio + idx;
        let htmlAcciones = `<div class="fila-acciones">
            <button class="boton-accion boton-accion--ver" onclick="window.verHistorial('${reg.of}')" title="Ver Historial">
                <svg viewBox="0 0 24 24" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>`;
        if (isAdmin) {
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
        <tr>
          <td data-col="of"><a class="celda-of-enlace" href="javascript:void(0)" onclick="window.verHistorial('${reg.of}')">${resaltarTexto(reg.of, palabrasBusqueda)}</a></td>
          <td data-col="ref"><span class="celda-of-referencia">${resaltarTexto(reg.ref, palabrasBusqueda)}</span></td>
          <td data-col="cliente"><span class="celda-cliente">${resaltarTexto(reg.cliente, palabrasBusqueda)}</span></td>
          <td data-col="desc"><span class="celda-descripcion" title="${reg.desc}">${resaltarTexto(reg.desc, palabrasBusqueda)}</span></td>
          <td data-col="estado"><span class="celda-estado ${CLASE_POR_ESTADO[reg.estado] || 'estado--pendiente'}">${reg.estado}</span></td>
          <td data-col="fecha">${resaltarTexto(reg.fecha, palabrasBusqueda)}</td>
          <td data-col="fecha_reg">${resaltarTexto(reg.fecha_reg, palabrasBusqueda)}</td>
          <td data-col="proceso">${resaltarTexto(reg.proceso, palabrasBusqueda)}</td>
          <td data-col="maquina"><span class="celda-maquina">${resaltarTexto(reg.maquina, palabrasBusqueda)}</span></td>
          <td data-col="tipo">${resaltarTexto(reg.tipo, palabrasBusqueda)}</td>
          <td data-col="ubicacion"><span class="celda-ubicacion">${SVG_PIN}${resaltarTexto(reg.ubicacion, palabrasBusqueda)}</span></td>
          <td data-col="sobre"><span class="celda-ubicacion">${SVG_PIN}${resaltarTexto(reg.sobre, palabrasBusqueda)}</span></td>
          <td data-col="cantidad">${resaltarTexto(reg.cantidad, palabrasBusqueda)}</td>
          <td data-col="horas">${resaltarTexto(reg.horas, palabrasBusqueda)}</td>
          <td data-col="responsables">${resaltarTexto(reg.responsables, palabrasBusqueda)}</td>
          <td data-col="peso_i">${resaltarTexto(reg.peso_i, palabrasBusqueda)}</td>
          <td data-col="peso_f">${resaltarTexto(reg.peso_f, palabrasBusqueda)}</td>
          <td data-col="perdida">${resaltarTexto(reg.perdida, palabrasBusqueda)}</td>
          <td data-col="temp">${resaltarTexto(reg.temp, palabrasBusqueda)}°C</td>
          <td data-col="rpm">${resaltarTexto(reg.rpm, palabrasBusqueda)}</td>
          <td data-col="tiempo_t">${resaltarTexto(reg.tiempo_t, palabrasBusqueda)}</td>
          <td data-col="comp">${resaltarTexto(reg.comp, palabrasBusqueda)}</td>
          <td data-col="acciones">${htmlAcciones}</td>
        </tr>
      `}).join('');
      
      // Aplicar visibilidad actual de columnas tras renderizar
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
       7. FUNCIONES GLOBALES (Exportadas)
    ============================================================ */

    window.verHistorial = function(of_num) {
        console.log("EIS: Abriendo historial comparativo de OF:", of_num);
        const modal = document.getElementById('modal-historial');
        const spanOF = document.getElementById('historial-of');
        const tbody = document.getElementById('historial-cuerpo');
        
        if (!modal || !spanOF || !tbody) return;

        spanOF.innerText = of_num;
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;">Cargando comparativa...</td></tr>';
        modal.style.display = 'flex';

        fetch(`/grabados/api/historial/${of_num}/`)
            .then(res => res.json())
            .then(res => {
                const principal = res.principal || [];
                const referencia = res.referencia || [];
                const refId = res.ref_id;

                if (principal.length === 0 && referencia.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;">No hay registros encontrados.</td></tr>';
                    return;
                }

                let html = "";

                const renderFila = (h, tipo) => {
                    const colorTipo = tipo === 'Principal' ? '#2d8a3e' : '#e67e22';
                    const datosTecnicos = `
                        <div style="font-size:10px;">
                            ${h.peso_inicial || '—'} / ${h.peso_final || '—'} kg<br>
                            ${h.temp || '—'}°C | ${h.rpm || '—'} RPM
                        </div>
                    `;
                    return `
                        <tr style="background-color: ${tipo === 'Principal' ? 'rgba(45,138,62,0.03)' : 'rgba(243,156,18,0.03)'};">
                            <td>${new Date(h.actualizado_el).toLocaleString()}</td>
                            <td style="font-weight:bold;"><span style="color:${colorTipo};">(${tipo})</span><br>${h.of}</td>
                            <td><strong>${h.proceso}</strong></td>
                            <td><span class="celda-estado ${CLASE_POR_ESTADO[h.estado] || ''}">${h.estado}</span></td>
                            <td>${datosTecnicos}</td>
                            <td style="text-align:center; font-weight:bold;">${h.usos_acumulados || 0}</td>
                            <td style="color:#666;">👤 ${h.usuario__username || 'Sistema'}</td>
                            <td>${h.ubicacion || '—'}</td>
                        </tr>
                    `;
                };

                // Renderizar Principal
                principal.forEach(h => html += renderFila(h, 'Principal'));

                // Renderizar Referencia
                if (referencia.length > 0) {
                    html += `<tr style="background: #f8f9fa;"><td colspan="8" style="text-align:center; font-weight:bold; font-size:11px; padding:10px; color:#666; border-top: 2px solid #ddd;">MOVIMIENTOS DE LA REFERENCIA: ${refId}</td></tr>`;
                    referencia.forEach(h => html += renderFila(h, 'Referencia'));
                } else if (refId) {
                    html += `<tr style="background: #fff5f5;"><td colspan="8" style="text-align:center; color:#d32f2f; font-size:11px; padding:10px;">⚠️ La referencia ${refId} no tiene movimientos registrados aún.</td></tr>`;
                }

                tbody.innerHTML = html;
            })
            .catch(err => {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:red;">Error de conexión.</td></tr>';
                console.error(err);
            });
    };

    window.cerrarHistorial = function() {
        document.getElementById('modal-historial').style.display = 'none';
    };

    window.alternarFiltrosPanel = function() {
        const panel = document.getElementById('filtros-panel');
        if (panel) panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    };

    window.limpiarTodosLosFiltros = function() {
        const e = document.getElementById('filtro-estado'), p = document.getElementById('filtro-proceso');
        if (e) e.value = ''; if (p) p.value = '';
        aplicarFiltrosCombinados();
    };

    window.irAPagina = function(n) { paginaActual = n; actualizarUI(); };

    window.editarRegistro = function(idx) {
        const reg = registrosFiltrados[idx];
        window.open(`/admin/gestion_grabados/ordenfabricacion/?q=${reg.of}`, '_blank');
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
        if (panel) panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
    };

    window.aplicarFiltroDeFecha = function() {
        const d = document.getElementById('fecha-desde').value;
        const h = document.getElementById('fecha-hasta').value;
        // Normalizamos a inicio y fin de día para que el rango sea inclusivo
        fechaDesdeTs = d ? new Date(d + 'T00:00:00').getTime() : null;
        fechaHastaTs = h ? new Date(h + 'T23:59:59').getTime() : null;
        
        const etiqueta = document.getElementById('etiqueta-fecha');
        if (etiqueta) etiqueta.innerText = (d || h) ? 'Filtrado' : 'Fecha';
        
        aplicarFiltrosCombinados();
    };

    window.limpiarFechas = function() {
        document.getElementById('fecha-desde').value = '';
        document.getElementById('fecha-hasta').value = '';
        window.aplicarFiltroDeFecha();
    };

    /* --- Funciones de UI adicionales --- */
    window.alternarExportar = function() {
        const panel = document.getElementById('exportar-panel');
        if (panel) panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
    };

    window.alternarColumnasPanel = function() {
        const panel = document.getElementById('columnas-panel');
        if (panel) panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
    };

    /* ============================================================
       8. INICIALIZACIÓN
    ============================================================ */
    
    // Inicializar input de fecha con el valor por defecto (3 meses atrás)
    const inputDesde = document.getElementById('fecha-desde');
    if (inputDesde) {
        inputDesde.value = fechaInicioDefault.toISOString().split('T')[0];
        const etiqueta = document.getElementById('etiqueta-fecha');
        if (etiqueta) etiqueta.innerText = 'Filtrado';
    }
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
        buscador.addEventListener('input', e => {
            palabrasBusqueda = e.target.value.toLowerCase().trim().split(/\s+/).filter(p => p);
            aplicarFiltrosCombinados();
        });
    }

    inicializarPanelColumnas();
    cargarDatosDesdeDB();
})();

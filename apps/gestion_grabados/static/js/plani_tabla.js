
/* ============================================================
   Lógica para la Tabla PLANI - SINCRONIZACIÓN AUTOMÁTICA
   ============================================================ */

let DATOS_PLANI = [];
let registrosFiltrados = [];
let paginaActual = 1;
const REGISTROS_POR_PAGINA = 10000; // Mostrar todo sin paginación
let registroActivoIndex = null;
let modoReporteDano = false;

function getCookie(name) {
    const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return match ? decodeURIComponent(match[2]) : null;
}

// Función que dispara la sincronización desde el servidor
function sincronizarConExcel() {
    const btn = document.getElementById('btn-sincronizar');
    const overlay = document.getElementById('overlay-proceso');
    const overlayTitulo = document.getElementById('overlay-titulo');
    const overlayMensaje = document.getElementById('overlay-mensaje');
    
    overlayTitulo.innerText = "Consultando Datos...";
    overlayMensaje.innerText = "Sincronizando Excel con bases de datos de producción...";
    overlay.style.display = 'flex';
    
    btn.disabled = true;
    const startTime = Date.now();

    fetch('/grabados/api/sincronizar/')
        .then(response => response.json())
        .then(res => {
            // Eliminamos la espera artificial (setTimeout) para que sea instantáneo
            if (res.status === 'ok') {
                DATOS_PLANI = res.data;
                registrosFiltrados = [...DATOS_PLANI];
                paginaActual = 1;
                renderizarTabla();
                
                const s = res.stats;
                overlayTitulo.innerText = "¡Sincronización Completada!";
                overlayMensaje.innerHTML = `
                    <div style="text-align: left; margin-top: 15px; background: #f9f9f9; padding: 15px; border-radius: 10px; border: 1px solid #ddd; max-height: 250px; overflow-y: auto;">
                        <p>📊 <strong>Total en Excel:</strong> ${s.total_filas_excel}</p>
                        <p style="color: #2d8a3e;">✅ <strong>Cargados con éxito:</strong> ${s.procesados_ok}</p>
                        <p style="color: #f39c12;">📑 <strong>Duplicados omitidos:</strong> ${s.duplicados_omitidos || 0}</p>
                        <p style="color: ${s.con_error > 0 ? '#d32f2f' : '#666'};">❌ <strong>Con errores/vacíos:</strong> ${s.con_error}</p>
                        ${s.errores_detalle && s.errores_detalle.length > 0 ? `
                            <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #eee; font-size: 11px; color: #d32f2f;">
                                <strong>Detalle de errores:</strong><br>
                                ${s.errores_detalle.slice(0, 5).map(e => `• ${e}`).join('<br>')}
                                ${s.errores_detalle.length > 5 ? '<br>... y más errores.' : ''}
                            </div>
                        ` : ''}
                    </div>
                    <button class="boton boton--primario" onclick="document.getElementById('overlay-proceso').style.display='none'" style="margin-top: 20px; width: 100%; background-color: #2d8a3e;">Continuar</button>
                `;

                // Ya no se cierra solo, el usuario debe pulsar 'Continuar'
            } else {
                overlay.style.display = 'none';
                alert('Error: ' + res.message);
            }
        })
        .catch(error => {
            overlay.style.display = 'none';
            console.error('Error:', error);
            alert('Error de conexión con el servidor.');
        })
        .finally(() => {
            btn.disabled = false;
        });
}

function renderizarTabla() {
    const inicio = (paginaActual - 1) * REGISTROS_POR_PAGINA;
    const pagina = registrosFiltrados.slice(inicio, inicio + REGISTROS_POR_PAGINA);
    const tbody = document.getElementById('tabla-cuerpo');
    
    if (!tbody) return;
    
    if (pagina.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="tabla-sin-resultados">Haga clic en "Actualizar" para ver la vista previa.</td></tr>';
        return;
    }

    tbody.innerHTML = pagina.map((reg, idx) => {
        const globalIdx = inicio + idx;
        
        // --- LÓGICA DE GESTIÓN UNIFICADA ---
        let gestionHTML = "";
        let estadoLabel = reg.estado_db || 'PENDIENTE';
        let estadoClase = 'estado--pendiente';

        if (estadoLabel === 'PENDIENTE') {
            gestionHTML = `<button class="boton boton--primario" onclick="abrirDashboard(${globalIdx})" style="padding: 5px 10px; font-size: 11px;">Iniciar Producción</button>`;
        } 
        else if (estadoLabel === 'EN_PROCESO') {
            estadoClase = 'estado--en-proceso';
            gestionHTML = `
                <div style="display:flex; flex-direction:column; gap:5px;">
                    <span class="celda-estado ${estadoClase}" onclick="abrirDashboard(${globalIdx})" style="cursor:pointer;" title="Clic para reportar daño">EN PREPARACIÓN ⚙️</span>
                    <div style="display:flex; gap:3px;">
                        <button class="boton" onclick="enviarAMaquina(${globalIdx})" style="flex:1; padding: 3px 5px; font-size: 9px; background-color: #f39c12; color: white;">A Máquina</button>
                        <button class="boton" onclick="abrirDashboard(${globalIdx})" style="flex:1; padding: 3px 5px; font-size: 9px; background-color: #d32f2f; color: white;">Reportar Daño</button>
                    </div>
                </div>
            `;
        }
        else if (estadoLabel === 'EN_MAQUINA') {
            estadoClase = 'estado--en-proceso'; // Usar color naranja/amarillo
            gestionHTML = `
                <div style="display:flex; flex-direction:column; gap:5px;">
                    <span class="celda-estado" style="background-color: #fff3e0; color: #e65100; border: 1px solid #ffe0b2;">EN MÁQUINA</span>
                    <small style="color: #666; font-size: 9px;">Esperando fin de proceso...</small>
                </div>
            `;
        }
        else if (estadoLabel === 'LISTO_PARA_RECOGER') {
            estadoClase = 'estado--en-revision'; // Azul
            gestionHTML = `
                <div style="display:flex; flex-direction:column; gap:5px;">
                    <span class="celda-estado" style="background-color: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb;">LISTO PARA RECOGER</span>
                    <button class="boton" onclick="abrirDashboard(${globalIdx})" style="padding: 5px 10px; font-size: 11px; background-color: #1565c0; color: white;">Recoger de Máquina</button>
                </div>
            `;
        }
        else if (estadoLabel === 'COMPLETADO') {
            estadoClase = 'estado--completado';
            gestionHTML = `
                <div style="display:flex; flex-direction:column; gap:2px;">
                    <span class="celda-estado ${estadoClase}">GUARDADO</span>
                    <small style="color: #2d8a3e; font-size: 10px; font-weight: bold;">📍 ${reg.ubicacion_db || 'Sin ubic.'}</small>
                </div>
            `;
        }
        else if (estadoLabel === 'REPETIR') {
            estadoClase = 'estado--cancelado';
            gestionHTML = `
                <div style="display:flex; flex-direction:column; gap:2px;">
                    <span class="celda-estado ${estadoClase}">PARA REPETIR</span>
                    <small style="color: #d32f2f; font-size: 9px;">Ver ficha para motivo</small>
                </div>
            `;
        }

        return `
            <tr class="fila-hover-global">
                <td><strong>${reg.of || '—'}</strong></td>
                <td style="font-weight: bold; color: #2d8a3e;">${reg.ref_ext || '—'}</td>
                <td>${reg.descripcion || '—'}</td>
                <td>
                    ${reg.cliente || '—'}
                    ${reg.responsable_db ? `<br><small style="color: #666;">👤 ${reg.responsable_db}</small>` : ''}
                </td>
                <td><span class="celda-estado estado--en-revision" style="background-color: #f1f8e9; color: #2e7d32; border: 1px solid #c8e6c9; font-weight: bold;">${reg.proceso || '—'}</span></td>
                <td>${gestionHTML}</td>
                <td>${reg.maquina || '—'}</td>
                <td>${reg.fecha_programada || '—'}</td>
            </tr>
        `;
    }).join('');
    actualizarInfoPie();
}

function enviarAMaquina(index) {
    const reg = registrosFiltrados[index];
    if (!confirm(`¿Confirmar que la OF ${reg.of} ya está en máquina?`)) return;

    fetch('/grabados/api/registrar/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({
            of: reg.of,
            proceso: reg.proceso,
            tipo_registro: 'ENVIAR_MAQUINA'
        })
    })
    .then(response => response.json())
    .then(res => {
        if (res.status === 'ok') {
            reg.estado_db = 'EN_MAQUINA';
            renderizarTabla();
        } else { alert("Error: " + res.message); }
    })
    .catch(err => alert("Error de conexión."));
}

function abrirDashboard(index) {
    const reg = registrosFiltrados[index];
    if (!reg) return;
    
    registroActivoIndex = index;
    modoReporteDano = false;

    document.getElementById('span-of').innerText = reg.of;
    document.getElementById('span-cliente').innerText = reg.cliente;
    
    const esListoRecoger = (reg.estado_db === 'LISTO_PARA_RECOGER');

    const formEntrada = document.getElementById('form-registro-entrada');
    const formProduccion = document.getElementById('form-registro-produccion');
    const tituloModal = document.getElementById('modal-registro').querySelector('h3');
    const btnGuardar = document.getElementById('btn-dashboard-guardar');
    const btnDano = document.getElementById('btn-reportar-dano');
    const seccionDano = document.getElementById('seccion-dano-fabricacion');

    seccionDano.style.display = 'none';
    btnGuardar.style.backgroundColor = '#2d8a3e';

    if (esListoRecoger) {
        tituloModal.innerText = "Finalizar y Guardar Grabado";
        btnGuardar.innerText = "Guardar en Almacén";
        btnDano.style.display = 'none';
        formEntrada.style.display = 'none';
        formProduccion.style.display = 'block';
    } else {
        tituloModal.innerText = "Iniciar Producción (Datos Técnicos)";
        btnGuardar.innerText = "Crear Registro";
        btnDano.style.display = 'block';
        formEntrada.style.display = 'block';
        formProduccion.style.display = 'none';
        
        document.getElementById('reg-responsable').value = reg.responsable || '';
        document.getElementById('prod-tiempo').value = reg.horas_proceso || '';
    }
    document.getElementById('modal-registro').style.display = 'flex';
}

function activarReporteDano() {
    modoReporteDano = true;
    document.getElementById('seccion-dano-fabricacion').style.display = 'block';
    const btnGuardar = document.getElementById('btn-dashboard-guardar');
    btnGuardar.innerText = "Guardar Reporte de Daño";
    btnGuardar.style.backgroundColor = '#d32f2f';
    document.getElementById('btn-reportar-dano').style.display = 'none';
    document.getElementById('reg-comentario-dano').focus();
}

function guardarDatosDashboard() {
    if (registroActivoIndex === null) return;
    const reg = registrosFiltrados[registroActivoIndex];
    const btnGuardar = document.getElementById('btn-dashboard-guardar');
    const esListoRecoger = (reg.estado_db === 'LISTO_PARA_RECOGER');

    // Usar FormData para poder enviar archivos (fotos)
    let formData = new FormData();
    formData.append('of', reg.of);
    formData.append('proceso', reg.proceso);
    formData.append('cliente', reg.cliente);
    formData.append('descripcion', reg.descripcion);
    formData.append('referencia', reg.ref_ext);
    formData.append('fecha_programada', reg.fecha_programada);

    if (esListoRecoger) {
        const ubicacion = document.getElementById('reg-ubicacion-grabado').value.trim();
        const estadoFisico = document.getElementById('reg-estado-fisico').value;
        const comentario = document.getElementById('reg-comentario').value.trim();

        if (!ubicacion) { alert("Ingrese ubicación física."); return; }
        
        if (estadoFisico === 'REPETIR' && comentario.length < 5) {
            alert("Para repetir el grabado, debe ingresar un comentario detallado del motivo.");
            return;
        }

        formData.append('tipo_registro', 'ALMACEN_RECOGER');
        formData.append('estado_fisico', estadoFisico);
        formData.append('ubicacion', ubicacion);
        formData.append('sobre', document.getElementById('reg-ubicacion-sobre').value);
        formData.append('comentario', comentario);
    } else {
        const responsable = document.getElementById('reg-responsable').value.trim();
        if (!responsable) { alert("Ingrese responsable."); return; }
        
        if (modoReporteDano) {
            const motivo = document.getElementById('reg-comentario-dano').value.trim();
            if (!motivo) { alert("Explique el motivo del daño."); return; }
            formData.append('tipo_registro', 'REPORTE_DANO');
            formData.append('comentario', motivo);
            
            // Adjuntar foto si existe
            const inputFoto = document.getElementById('reg-foto-dano');
            if (inputFoto && inputFoto.files[0]) {
                formData.append('foto_dano', inputFoto.files[0]);
            }
        } else {
            formData.append('tipo_registro', 'CREAR_FABRICACION');
        }
        formData.append('responsable', responsable);
        formData.append('tiempo', document.getElementById('prod-tiempo').value);
        formData.append('peso_i', document.getElementById('prod-peso-i').value);
        formData.append('peso_f', document.getElementById('prod-peso-f').value);
        formData.append('temp', document.getElementById('prod-temp').value);
        formData.append('rpm', document.getElementById('prod-rpm').value);
        formData.append('compensacion', document.getElementById('prod-compensacion').value);
    }

    btnGuardar.disabled = true;
    fetch('/grabados/api/registrar/', {
        method: 'POST',
        // Nota: Al usar FormData no se pone el header Content-Type (el navegador lo hace solo con el boundary)
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
        body: formData
    })
    .then(response => response.json())
    .then(res => {
        if (res.status === 'ok') {
            alert(res.message);
            // Actualizar estado localmente
            if (formData.get('tipo_registro') === 'CREAR_FABRICACION') reg.estado_db = 'EN_PROCESO';
            else if (formData.get('tipo_registro') === 'ALMACEN_RECOGER') {
                reg.estado_db = (formData.get('estado_fisico') === 'REPETIR') ? 'REPETIR' : 'COMPLETADO';
                reg.ubicacion_db = formData.get('ubicacion');
            }
            reg.responsable_db = formData.get('responsable') || reg.responsable_db;
            
            renderizarTabla();
            cerrarDashboard();
        } else { alert("Error: " + res.message); }
    })
    .catch(err => alert("Error de conexión al guardar."))
    .finally(() => { btnGuardar.disabled = false; });
}

function calcularPerdida() {
    const pi = parseFloat(document.getElementById('prod-peso-i').value) || 0;
    const pf = parseFloat(document.getElementById('prod-peso-f').value) || 0;
    const perdida = Math.max(0, pi - pf);
    
    // Actualizar mensaje de pérdida en kg
    const msgPerdida = document.getElementById('prod-perdida-msg');
    if (msgPerdida) msgPerdida.innerText = perdida.toFixed(2) + ' kg';

    // Calcular compensación (Baño): Pérdida * 6.6
    const mlBano = perdida * 6.6;
    const msgBano = document.getElementById('prod-bano-msg');
    if (msgBano) msgBano.innerText = mlBano.toFixed(0) + ' ml';

    // Rellenar automáticamente el campo de compensación como sugerencia
    const inputComp = document.getElementById('prod-compensacion');
    if (inputComp) {
        inputComp.value = mlBano > 0 ? `${mlBano.toFixed(0)} ml de baño` : '';
    }
}

function cerrarDashboard() {
    const modal = document.getElementById('modal-registro');
    if (modal) modal.style.display = 'none';
}

function actualizarInfoPie() {
    const info = document.getElementById('pie-info');
    if (info) info.innerHTML = `Vista Previa (Total: ${registrosFiltrados.length} registros en Excel)`;
}

const buscador = document.getElementById('buscador-input');
if (buscador) {
    buscador.addEventListener('input', function(e) {
        const b = e.target.value.toLowerCase();
        registrosFiltrados = DATOS_PLANI.filter(r => 
            String(r.of).toLowerCase().includes(b) || 
            String(r.cliente).toLowerCase().includes(b)
        );
        paginaActual = 1;
        renderizarTabla();
    });
}

// Inicialización
renderizarTabla();

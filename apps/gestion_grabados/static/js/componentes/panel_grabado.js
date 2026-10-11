/* ============================================================
   Componente reutilizable: panel lateral con el reporte de un grabado.
   Requiere static/css/componentes/panel_grabado.css.
   Lee GET /grabados/api/grabado/<id>/detalle/ (solo lectura).

   Uso:
     PanelGrabado.abrir(grabadoId);                  // solo consulta
     PanelGrabado.abrir(grabadoId, { orden });       // + datos de una fila de la Tabla de Consultas
     PanelGrabado.abrirOrden(orden);                 // OF sin grabado: la fila + GET /grabados/api/historial/<of>/
     PanelGrabado.abrir(grabadoId, {                 // con botones de K1
         acciones: {
             onAprobar:  (k1, detalle) => { ... },
             onRechazar: (k1, detalle) => { ... },
         },
     });
     PanelGrabado.cerrar();
     PanelGrabado.recargar();

   Los botones Aprobar/Rechazar solo aparecen si la pantalla pasa `acciones`
   y el grabado tiene un K1 pendiente (k1_actual) y no está inactivo. Cualquier
   usuario del módulo puede decidir, incluso sobre una fabricación que registró él.

   Desactivar / Reactivar aparece siempre en el pie (un grabado nunca se elimina).
   Después de un cambio el panel se recarga y avisa a la pantalla con el evento
   `panelgrabado:cambio` en document (detail: {id, estado}) para que refresque su lista.

   `orden` es una fila de OrdenFabricacion tal como la devuelve
   /grabados/api/registros/ (of, ref, proceso, estado, cliente, desc, ...).
   ============================================================ */

(function () {
    'use strict';

    const URL_DETALLE = (id) => `/grabados/api/grabado/${encodeURIComponent(id)}/detalle/`;
    const URL_HISTORIAL_OF = (of) => `/grabados/api/historial/${encodeURIComponent(of)}/`;
    const URL_DESACTIVAR = (id) => `/grabados/api/grabado/${encodeURIComponent(id)}/desactivar/`;
    const URL_REACTIVAR = (id) => `/grabados/api/grabado/${encodeURIComponent(id)}/reactivar/`;
    const LARGO_MINIMO_MOTIVO = 5;   // services.LARGO_MINIMO_MOTIVO_DESACTIVACION

    let fondo = null;
    let panel = null;
    let opcionesActuales = {};
    let grabadoActual = null;
    let ordenActual = null;        // fila de la Tabla de Consultas (opcional)
    let pedidoActual = 0;          // descarta respuestas viejas si se abre otro grabado
    let focoAnterior = null;
    let temporizadorCierre = null;

    // ---------------------------------------------------------- utilidades
    function escapar(valor) {
        const div = document.createElement('div');
        div.textContent = valor === null || valor === undefined || valor === '' ? '—' : String(valor);
        return div.innerHTML;
    }

    function numero(valor, unidad) {
        if (valor === null || valor === undefined || valor === '') return '—';
        const n = Number(valor);
        const texto = Number.isFinite(n)
            ? n.toLocaleString('es-DO', { maximumFractionDigits: 2 })
            : String(valor);
        return escapar(texto) + (unidad ? `<span class="pg-unidad">${escapar(unidad)}</span>` : '');
    }

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function etiqueta(codigo, texto) {
        return `<span class="pg-etiqueta pg-etiqueta--${escapar(codigo)}">${escapar(texto)}</span>`;
    }

    function dato(titulo, valorHtml, ancho) {
        return `<div class="pg-dato${ancho ? ' pg-dato--ancho' : ''}"><dt>${escapar(titulo)}</dt><dd>${valorHtml}</dd></div>`;
    }

    function seccion(titulo, contenido) {
        return `<section class="pg-seccion"><h3 class="pg-seccion__titulo">${escapar(titulo)}</h3>${contenido}</section>`;
    }

    // ------------------------------------------------------------- secciones
    function htmlEncabezado(d) {
        const origen = d.datos_manuales
            ? etiqueta('neutra', 'Cargados a mano')
            : 'Sistema externo';
        return seccion('Grabado', `
            <dl class="pg-datos">
                ${dato('OF de origen', escapar(d.of_origen))}
                ${dato('Proceso', escapar(d.proceso))}
                ${dato('Estado', etiqueta(d.estado, d.estado_display))}
                ${dato('Tipo', etiqueta('tipo-' + d.tipo, d.tipo_display))}
                ${dato('Cliente', escapar(d.cliente), true)}
                ${dato('Descripción', escapar(d.referencia), true)}
                ${dato('Sobre', escapar(d.sobre))}
                ${dato('Origen de los datos', origen)}            </dl>`);
    }

    // Grabados que no pasan por K1 (Grabado.tipo DIRECTO o LEGADO).
    function sinK1(d) {
        return d.tipo === 'DIRECTO'
            ? 'Grabado de producción directa: se aprobó al crearlo, sin K1.'
            : 'Grabado migrado del sistema anterior: nunca pasó K1 en EIS.';
    }

    function htmlDesactivacion(d) {
        const x = d.desactivacion;
        if (!x) return '';
        return `<section class="pg-seccion pg-seccion--inactivo">
            <h3 class="pg-seccion__titulo">Grabado inactivo</h3>
            <dl class="pg-datos">
                ${dato('Desactivado por', escapar(x.por))}
                ${dato('Fecha', escapar(x.el))}
                ${dato('Al reactivar vuelve a', etiqueta(x.estado_previo, x.estado_previo_display))}
                ${dato('Motivo', escapar(x.motivo), true)}
            </dl>
        </section>`;
    }

    function htmlHistorialActivaciones(d) {
        const cambios = d.historial_activaciones || [];
        if (!cambios.length) return '';
        const items = cambios.map(c => {
            const desactivo = c.accion === 'DESACTIVAR';
            return `
            <li class="pg-item">
                <div class="pg-item__linea">
                    <span class="pg-item__titulo">${escapar(c.accion_display)}</span>
                    ${desactivo ? etiqueta('neutra', 'Estaba en ' + c.estado_display) : etiqueta(c.estado, 'Volvió a ' + c.estado_display)}
                </div>
                <div class="pg-item__meta">${escapar(c.fecha)} · por ${escapar(c.usuario)}</div>
                ${c.motivo ? `<div class="pg-item__motivo"><strong>Motivo:</strong> ${escapar(c.motivo)}</div>` : ''}
            </li>`;
        }).join('');
        return seccion('Historial de activación', `<ul class="pg-lista">${items}</ul>`);
    }

    function htmlK1Actual(d) {
        const k1 = d.k1_actual;
        if (!k1) {
            const texto = d.tipo === 'K1' ? 'Este grabado no tiene un K1 pendiente.' : sinK1(d);
            return seccion('K1 actual', `<p class="pg-vacio">${escapar(texto)}</p>`);
        }
        const aviso = d.inactivo
            ? '<p class="pg-vacio" style="margin-top:10px;">No se puede decidir mientras el grabado esté inactivo: '
              + 'no aparece en K1 Pendientes hasta que lo reactives.</p>'
            : '';
        return seccion('K1 actual', `
            <dl class="pg-datos">
                ${dato('Intento', escapar(k1.intento))}
                ${dato('Máquina', escapar(k1.maquina))}
                ${dato('Resultado', etiqueta(k1.resultado, k1.resultado_display))}
                ${dato('Registrado por', escapar(k1.creado_por))}
                ${dato('Fecha', escapar(k1.creado_el))}
            </dl>${aviso}`);
    }

    function htmlTecnicos(t) {
        return `
            <dl class="pg-datos">
                ${dato('Responsables', escapar(t.responsables), true)}
                ${dato('Peso inicial', numero(t.peso_inicial, 'g'))}
                ${dato('Peso final', numero(t.peso_final, 'g'))}
                ${dato('Pérdida', numero(t.perdida, 'g'))}
                ${dato('Temperatura', numero(t.temp, 'ºC'))}
                ${dato('RPM', numero(t.rpm))}
                ${dato('Tiempo', numero(t.tiempo, 'min'))}
                ${dato('Compensación', numero(t.compensacion, 'ml'))}
                ${dato('Baño', numero(t.bano_ml, 'ml'))}
                ${t.compensacion_motivo ? dato('Motivo de la compensación', escapar(t.compensacion_motivo), true) : ''}
            </dl>`;
    }

    function htmlTecnicosActuales(d) {
        if (d.k1_actual) {
            const f = d.k1_actual.fabricacion;
            return seccion(`Datos técnicos de la fabricación en prueba (n.º ${f.numero})`, htmlTecnicos(f.tecnicos));
        }
        const ultima = d.fabricaciones[d.fabricaciones.length - 1];
        if (!ultima) return '';
        return seccion(`Datos técnicos de la última fabricación (n.º ${ultima.numero})`, htmlTecnicos(ultima.tecnicos));
    }

    function htmlFabricaciones(d) {
        if (!d.fabricaciones.length) {
            return seccion('Historial de fabricaciones', '<p class="pg-vacio">Sin fabricaciones registradas.</p>');
        }
        const items = d.fabricaciones.slice().reverse().map(f => `
            <li class="pg-item">
                <div class="pg-item__linea">
                    <span class="pg-item__titulo">N.º ${escapar(f.numero)}</span>
                    ${etiqueta('neutra', f.tipo_display)}
                </div>
                <div class="pg-item__meta">${escapar(f.registrado_el)} · por ${escapar(f.registrado_por)}</div>
                <details class="pg-detalles">
                    <summary>Ver datos técnicos</summary>
                    ${htmlTecnicos(f.tecnicos)}
                </details>
            </li>`).join('');
        return seccion('Historial de fabricaciones', `<ul class="pg-lista">${items}</ul>`);
    }

    // Decidido por quien registró la fabricación (solo posible para superusuarios).
    function textoResultado(p) {
        if (!p.auto_decision) return p.resultado_display;
        return p.resultado === 'APROBADO' ? 'Auto-aprobado' : 'Auto-rechazado';
    }

    function htmlIntentosK1(d) {
        if (!d.pruebas_k1.length) {
            const texto = d.tipo === 'K1' ? 'Sin pruebas K1 registradas.' : sinK1(d);
            return seccion('Historial de intentos K1', `<p class="pg-vacio">${escapar(texto)}</p>`);
        }
        const items = d.pruebas_k1.slice().reverse().map(p => {
            const decision = p.decidido_por
                ? `Decidió ${escapar(p.decidido_por)} el ${escapar(p.decidido_el)}`
                : 'Sin decisión todavía';
            return `
                <li class="pg-item">
                    <div class="pg-item__linea">
                        <span class="pg-item__titulo">Intento ${escapar(p.intento)}</span>
                        ${etiqueta(p.resultado, textoResultado(p))}
                    </div>
                    <div class="pg-item__meta">Máquina ${escapar(p.maquina)} · fabricación n.º ${escapar(p.fabricacion_numero)}</div>
                    <div class="pg-item__meta">${decision}</div>
                    ${p.motivo_rechazo ? `<div class="pg-item__motivo"><strong>Motivo:</strong> ${escapar(p.motivo_rechazo)}</div>` : ''}
                </li>`;
        }).join('');
        return seccion('Historial de intentos K1', `<ul class="pg-lista">${items}</ul>`);
    }

    function htmlEnvios(d) {
        const resumen = `
            <dl class="pg-datos pg-datos--dos">
                ${dato('Usos acumulados', numero(d.usos_acumulados))}
                ${dato('Ubicación', escapar(d.ubicacion))}
            </dl>`;
        if (!d.envios.length) {
            return seccion('Envíos a máquina y uso', resumen + '<p class="pg-vacio" style="margin-top:12px;">Sin envíos a máquina registrados.</p>');
        }
        const items = d.envios.map(e => `
            <li class="pg-item">
                <div class="pg-item__linea">
                    <span class="pg-item__titulo">OF ${escapar(e.of)}</span>
                    ${e.abierto ? etiqueta('EN_MAQUINA', 'En máquina') : etiqueta(e.estado_fisico === 'REPETIR' ? 'REPETIR' : 'APROBADO', e.estado_fisico || 'Recogido')}
                </div>
                <div class="pg-item__meta">Máquina ${escapar(e.maquina)} · enviado ${escapar(e.enviado_el)} por ${escapar(e.enviado_por)}</div>
                ${e.abierto ? '' : `<div class="pg-item__meta">Recogido ${escapar(e.recogido_el)} por ${escapar(e.recogido_por)}${e.ubicacion ? ' · ' + escapar(e.ubicacion) : ''}</div>`}
                ${e.comentario ? `<div class="pg-item__meta">${escapar(e.comentario)}</div>` : ''}
            </li>`).join('');
        return seccion('Envíos a máquina y uso', resumen + `<ul class="pg-lista" style="margin-top:12px;">${items}</ul>`);
    }

    function htmlHistorialUbicaciones(d) {
        const cambios = d.historial_ubicaciones || [];
        if (!cambios.length) {
            return seccion('Historial de ubicaciones', '<p class="pg-vacio">Sin cambios de ubicación registrados.</p>');
        }
        const items = cambios.map(c => `
            <li class="pg-item">
                <div class="pg-item__linea">
                    <span class="pg-item__titulo">${escapar(c.anterior)} → ${escapar(c.nueva)}</span>
                    ${etiqueta('neutra', c.origen_display)}
                </div>
                <div class="pg-item__meta">${escapar(c.cambiado_el)} · por ${escapar(c.usuario)}</div>
            </li>`).join('');
        return seccion('Historial de ubicaciones', `<ul class="pg-lista">${items}</ul>`);
    }

    // ------------------------------------------- orden (Tabla de Consultas)
    const TEXTO_ENLACE = {
        LEGADO: 'Migrada a EIS (enlace guardado por la migración).',
        ENVIO: 'Se tiró con este grabado desde el PLANI.',
        OF: 'Grabado de su OF de origen (OF Referencia o la propia OF).',
    };

    function htmlOrden(o) {
        const tecnicos = htmlTecnicos({
            responsables: o.responsables, peso_inicial: o.peso_i, peso_final: o.peso_f,
            perdida: o.perdida, temp: o.temp, rpm: o.rpm, tiempo: o.tiempo_t, compensacion: o.comp,
        });
        const enlace = o.grabado
            ? dato('Enlace con el grabado', escapar(TEXTO_ENLACE[o.enlace] || o.enlace), true)
            : dato('Grabado', `<span class="pg-aviso-orden">${escapar(o.motivo_sin_grabado)}</span>`, true);
        return seccion(`Orden de fabricación ${o.of} (histórico)`, `
            <dl class="pg-datos">
                ${dato('OF', escapar(o.of))}
                ${dato('OF Referencia', escapar(o.ref))}
                ${dato('Proceso', escapar(o.proceso))}
                ${dato('Estado (histórico)', etiqueta('neutra', o.estado || '—'))}
                ${dato('Cliente', escapar(o.cliente), true)}
                ${dato('Descripción', escapar(o.desc), true)}
                ${dato('Fecha prog.', escapar(o.fecha))}
                ${dato('Fecha registro', escapar(o.fecha_reg))}
                ${dato('Máquina', escapar(o.maquina))}
                ${dato('Tipo de grabado', escapar(o.tipo))}
                ${dato('Sobre', escapar(o.sobre))}
                ${dato('Cantidad', numero(o.cantidad))}
                ${dato('Horas', numero(o.horas))}
                ${dato('Usos (fila)', numero(o.usos_acumulados))}
                ${o.grabado ? '' : dato('Ubicación (histórica)', escapar(o.ubicacion))}
                ${enlace}
            </dl>
            <details class="pg-detalles">
                <summary>Ver datos técnicos de la orden</summary>
                ${tecnicos}
            </details>`);
    }

    function htmlMovimientosOF(res) {
        const fila = (h, rotulo) => `
            <li class="pg-item">
                <div class="pg-item__linea">
                    <span class="pg-item__titulo">OF ${escapar(h.of)} · ${escapar(h.proceso)}</span>
                    ${etiqueta('neutra', h.estado || '—')}
                    ${rotulo ? etiqueta('neutra', rotulo) : ''}
                </div>
                <div class="pg-item__meta">${escapar(h.actualizado_el ? new Date(h.actualizado_el).toLocaleString('es-DO') : null)} · por ${escapar(h.usuario__username || 'Sistema')}</div>
                <div class="pg-item__meta">Ubicación ${escapar(h.ubicacion)} · usos ${escapar(h.usos_acumulados || 0)}</div>
            </li>`;
        const principal = res.principal || [];
        const referencia = res.referencia || [];
        let html = principal.length
            ? `<ul class="pg-lista">${principal.map(h => fila(h)).join('')}</ul>`
            : '<p class="pg-vacio">Sin movimientos registrados.</p>';
        if (res.ref_id) {
            html += `<h4 class="pg-subtitulo">Movimientos de la referencia ${escapar(res.ref_id)}</h4>`;
            html += referencia.length
                ? `<ul class="pg-lista">${referencia.map(h => fila(h, 'Referencia')).join('')}</ul>`
                : `<p class="pg-vacio">La referencia ${escapar(res.ref_id)} no tiene movimientos registrados.</p>`;
        }
        return seccion('Historial de la OF (sistema anterior)', html);
    }

    // --------------------------------------------------------------- render
    function renderizarOrden(o, res) {
        panel.querySelector('.pg-cabecera__titulo').textContent = `OF ${o.of} · ${o.proceso || ''}`;
        panel.querySelector('.pg-cabecera__subtitulo').textContent = o.cliente || '';
        panel.querySelector('.pg-cuerpo').innerHTML =
            htmlOrden(o) + htmlMovimientosOF(res) +
            `<p class="pg-solo-impresion">Reporte generado el ${escapar(new Date().toLocaleString('es-DO'))}</p>`;
        panel.querySelector('.pg-pie').innerHTML = '';
    }

    function renderizar(d) {
        panel.querySelector('.pg-cabecera__titulo').textContent = `Grabado ${d.of_origen} · ${d.proceso}`;
        panel.querySelector('.pg-cabecera__subtitulo').textContent = d.cliente || '';
        panel.querySelector('.pg-cuerpo').innerHTML =
            (ordenActual ? htmlOrden(ordenActual) : '') +
            htmlEncabezado(d) + htmlDesactivacion(d) + htmlK1Actual(d) + htmlTecnicosActuales(d) +
            htmlFabricaciones(d) + htmlIntentosK1(d) + htmlEnvios(d) + htmlHistorialUbicaciones(d) +
            htmlHistorialActivaciones(d) +
            `<p class="pg-solo-impresion">Reporte generado el ${escapar(new Date().toLocaleString('es-DO'))}</p>`;
        renderizarAcciones(d);
    }

    function renderizarAcciones(d) {
        const pie = panel.querySelector('.pg-pie');
        pie.innerHTML = '';
        pie.append(botonActivacion(d));
        const acciones = opcionesActuales.acciones;
        const k1 = d.k1_actual;
        if (!acciones || !k1 || d.inactivo) return;

        const rechazar = document.createElement('button');
        rechazar.type = 'button';
        rechazar.className = 'pg-boton pg-boton--rechazar';
        rechazar.textContent = 'Rechazar';
        rechazar.addEventListener('click', () => acciones.onRechazar && acciones.onRechazar(k1, d));

        const aprobar = document.createElement('button');
        aprobar.type = 'button';
        aprobar.className = 'pg-boton pg-boton--aprobar';
        aprobar.textContent = 'Aprobar';
        aprobar.addEventListener('click', () => acciones.onAprobar && acciones.onAprobar(k1, d));

        pie.append(rechazar, aprobar);
    }

    // ------------------------------------------------- desactivar / reactivar
    function boton(texto, clase, alHacerClic) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'pg-boton ' + clase;
        b.textContent = texto;
        b.addEventListener('click', alHacerClic);
        return b;
    }

    function botonActivacion(d) {
        if (d.inactivo) {
            return boton('Reactivar', 'pg-boton--reactivar',
                         (e) => cambiarActivacion(d, URL_REACTIVAR(d.id), {}, e.currentTarget, null));
        }
        const desactivar = boton('Desactivar', 'pg-boton--desactivar', () => mostrarFormDesactivar(d));
        if (!d.puede_desactivar) {
            desactivar.disabled = true;
            desactivar.title = 'El grabado está en máquina: recógelo desde el PLANI antes de desactivarlo.';
        }
        return desactivar;
    }

    function mostrarFormDesactivar(d) {
        const pie = panel.querySelector('.pg-pie');
        pie.innerHTML = `
            <div class="pg-form-desactivar">
                <label for="pg-motivo-desactivar" class="pg-form-desactivar__etiqueta">
                    Motivo de la desactivación (obligatorio)</label>
                <textarea id="pg-motivo-desactivar" rows="2" maxlength="500"
                          placeholder="Ej.: grabado dañado, el cliente ya no lo usa..."></textarea>
                <p class="pg-form-desactivar__error" role="alert"></p>
            </div>`;
        const motivo = pie.querySelector('textarea');
        const error = pie.querySelector('.pg-form-desactivar__error');
        const cancelar = boton('Cancelar', 'pg-boton--neutro', () => renderizarAcciones(d));
        const confirmar = boton('Confirmar desactivación', 'pg-boton--desactivar', () => {
            const texto = motivo.value.trim();
            if (texto.length < LARGO_MINIMO_MOTIVO) {
                error.textContent = `Escribe el motivo (al menos ${LARGO_MINIMO_MOTIVO} caracteres).`;
                motivo.focus();
                return;
            }
            cambiarActivacion(d, URL_DESACTIVAR(d.id), { motivo: texto }, confirmar, error);
        });
        pie.append(cancelar, confirmar);
        motivo.focus();
    }

    function cambiarActivacion(d, url, cuerpo, botonUsado, error) {
        botonUsado.disabled = true;
        const mostrarError = (mensaje) => {
            botonUsado.disabled = false;
            if (error) { error.textContent = mensaje; return; }
            botonUsado.title = mensaje;
            const aviso = document.createElement('p');
            aviso.className = 'pg-form-desactivar__error';
            aviso.setAttribute('role', 'alert');
            aviso.textContent = mensaje;
            botonUsado.before(aviso);
        };
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json',
                       'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify(cuerpo),
        })
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') { mostrarError(res.message || 'No se pudo cambiar el grabado.'); return; }
                document.dispatchEvent(new CustomEvent('panelgrabado:cambio',
                                                       { detail: { id: d.id, estado: res.estado } }));
                recargar();
            })
            .catch(() => mostrarError('Error de conexión. Vuelve a intentarlo.'));
    }

    function mostrarCarga() {
        panel.querySelector('.pg-cabecera__titulo').textContent = 'Grabado';
        panel.querySelector('.pg-cabecera__subtitulo').textContent = '';
        panel.querySelector('.pg-cuerpo').innerHTML = '<div class="pg-estado-carga">Cargando reporte...</div>';
        panel.querySelector('.pg-pie').innerHTML = '';
    }

    function mostrarError(mensaje) {
        panel.querySelector('.pg-cuerpo').innerHTML = `<div class="pg-estado-error">${escapar(mensaje)}</div>`;
    }

    function cargar() {
        const pedido = ++pedidoActual;
        mostrarCarga();
        if (grabadoActual === null) { cargarOrden(pedido); return; }
        fetch(URL_DETALLE(grabadoActual), { headers: { 'Accept': 'application/json' } })
            .then(r => r.json())
            .then(res => {
                if (pedido !== pedidoActual) return;
                if (res.status !== 'ok') { mostrarError(res.message || 'No se pudo cargar el grabado.'); return; }
                renderizar(res.data);
            })
            .catch(() => { if (pedido === pedidoActual) mostrarError('Error de conexión al cargar el grabado.'); });
    }

    function cargarOrden(pedido) {
        fetch(URL_HISTORIAL_OF(ordenActual.of), { headers: { 'Accept': 'application/json' } })
            .then(r => { if (!r.ok) throw new Error(); return r.json(); })
            .then(res => { if (pedido === pedidoActual) renderizarOrden(ordenActual, res); })
            .catch(() => { if (pedido === pedidoActual) mostrarError('Error de conexión al cargar la orden.'); });
    }

    // ------------------------------------------------------------ estructura
    function construir() {
        if (panel) return;
        fondo = document.createElement('div');
        fondo.className = 'pg-fondo';
        fondo.hidden = true;
        fondo.addEventListener('click', cerrar);

        panel = document.createElement('aside');
        panel.className = 'pg-panel';
        panel.hidden = true;
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-modal', 'true');
        panel.setAttribute('aria-labelledby', 'pg-titulo');
        panel.innerHTML = `
            <header class="pg-cabecera">
                <div style="min-width:0;">
                    <h2 class="pg-cabecera__titulo" id="pg-titulo">Grabado</h2>
                    <p class="pg-cabecera__subtitulo"></p>
                </div>
                <div class="pg-cabecera__botones">
                    <button type="button" class="pg-boton-cabecera pg-boton-cabecera--imprimir">Imprimir</button>
                    <button type="button" class="pg-boton-cabecera pg-boton-cabecera--cerrar" aria-label="Cerrar">✕</button>
                </div>
            </header>
            <div class="pg-cuerpo"></div>
            <footer class="pg-pie"></footer>`;
        panel.querySelector('.pg-boton-cabecera--cerrar').addEventListener('click', cerrar);
        panel.querySelector('.pg-boton-cabecera--imprimir').addEventListener('click', imprimir);

        // Esc cierra solo si el foco está en el panel (no si hay un modal encima).
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !panel.hidden && panel.contains(document.activeElement)) cerrar();
        });

        document.body.append(fondo, panel);
    }

    function imprimir() {
        // Los <details> cerrados no se imprimen: se abren todos y se restauran después.
        const cerrados = [...panel.querySelectorAll('details:not([open])')];
        cerrados.forEach(d => { d.open = true; });
        document.body.classList.add('pg-imprimiendo');
        const restaurar = () => {
            document.body.classList.remove('pg-imprimiendo');
            cerrados.forEach(d => { d.open = false; });
            window.removeEventListener('afterprint', restaurar);
        };
        window.addEventListener('afterprint', restaurar);
        window.print();
    }

    // -------------------------------------------------------------- pública
    function abrir(grabadoId, opciones) {
        mostrar(grabadoId, opciones || {});
    }

    function abrirOrden(orden) {
        mostrar(null, { orden });
    }

    function mostrar(grabadoId, opciones) {
        construir();
        clearTimeout(temporizadorCierre);
        opcionesActuales = opciones;
        grabadoActual = grabadoId;
        ordenActual = opciones.orden || null;
        if (panel.hidden) focoAnterior = document.activeElement;

        fondo.hidden = false;
        panel.hidden = false;
        document.body.classList.add('pg-bloqueo-scroll');
        requestAnimationFrame(() => {
            fondo.classList.add('pg-fondo--visible');
            panel.classList.add('pg-panel--abierto');
        });
        panel.querySelector('.pg-boton-cabecera--cerrar').focus();
        cargar();
    }

    function cerrar() {
        if (!panel || panel.hidden) return;
        pedidoActual++;
        fondo.classList.remove('pg-fondo--visible');
        panel.classList.remove('pg-panel--abierto');
        document.body.classList.remove('pg-bloqueo-scroll');
        temporizadorCierre = setTimeout(() => { fondo.hidden = true; panel.hidden = true; }, 280);
        if (focoAnterior && typeof focoAnterior.focus === 'function') focoAnterior.focus();
    }

    function recargar() {
        if (panel && !panel.hidden && (grabadoActual !== null || ordenActual)) cargar();
    }

    window.PanelGrabado = { abrir, abrirOrden, cerrar, recargar };
})();

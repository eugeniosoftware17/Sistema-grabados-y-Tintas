/* ============================================================
   Componente reutilizable: panel lateral con el reporte de un grabado.
   Requiere static/css/componentes/panel_grabado.css.
   Lee GET /grabados/api/grabado/<id>/detalle/ (solo lectura).

   Uso:
     PanelGrabado.abrir(grabadoId);                  // solo consulta
     PanelGrabado.abrir(grabadoId, {                 // con botones de K1
         acciones: {
             onAprobar:  (k1, detalle) => { ... },
             onRechazar: (k1, detalle) => { ... },
         },
     });
     PanelGrabado.cerrar();
     PanelGrabado.recargar();

   Los botones Aprobar/Rechazar solo aparecen si la pantalla pasa `acciones`
   y el servidor dice que el usuario puede decidir (k1_actual.puede_decidir);
   la regla real se valida igual en el servidor al decidir. Si además
   k1_actual.es_propio (superusuario sobre su propia fabricación), la pantalla
   debe pedir confirmación antes de decidir: el callback recibe k1.es_propio.
   ============================================================ */

(function () {
    'use strict';

    const URL_DETALLE = (id) => `/grabados/api/grabado/${encodeURIComponent(id)}/detalle/`;

    let fondo = null;
    let panel = null;
    let opcionesActuales = {};
    let grabadoActual = null;
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
                ${dato('Referencia', escapar(d.referencia), true)}
                ${dato('Sobre', escapar(d.sobre))}
                ${dato('Origen de los datos', origen)}            </dl>`);
    }

    // Grabados que no pasan por K1 (Grabado.tipo DIRECTO o LEGADO).
    function sinK1(d) {
        return d.tipo === 'DIRECTO'
            ? 'Grabado de producción directa: se aprobó al crearlo, sin K1.'
            : 'Grabado migrado del sistema anterior: nunca pasó K1 en EIS.';
    }

    function htmlK1Actual(d) {
        const k1 = d.k1_actual;
        if (!k1) {
            const texto = d.tipo === 'K1' ? 'Este grabado no tiene un K1 pendiente.' : sinK1(d);
            return seccion('K1 actual', `<p class="pg-vacio">${escapar(texto)}</p>`);
        }
        return seccion('K1 actual', `
            <dl class="pg-datos">
                ${dato('Intento', escapar(k1.intento))}
                ${dato('Máquina', escapar(k1.maquina))}
                ${dato('Resultado', etiqueta(k1.resultado, k1.resultado_display))}
                ${dato('Registrado por', escapar(k1.creado_por))}
                ${dato('Fecha', escapar(k1.creado_el))}
            </dl>`);
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

    // --------------------------------------------------------------- render
    function renderizar(d) {
        panel.querySelector('.pg-cabecera__titulo').textContent = `Grabado ${d.of_origen} · ${d.proceso}`;
        panel.querySelector('.pg-cabecera__subtitulo').textContent = d.cliente || '';
        panel.querySelector('.pg-cuerpo').innerHTML =
            htmlEncabezado(d) + htmlK1Actual(d) + htmlTecnicosActuales(d) +
            htmlFabricaciones(d) + htmlIntentosK1(d) + htmlEnvios(d) +
            `<p class="pg-solo-impresion">Reporte generado el ${escapar(new Date().toLocaleString('es-DO'))}</p>`;
        renderizarAcciones(d);
    }

    function renderizarAcciones(d) {
        const pie = panel.querySelector('.pg-pie');
        pie.innerHTML = '';
        const acciones = opcionesActuales.acciones;
        const k1 = d.k1_actual;
        if (!acciones || !k1) return;

        if (!k1.puede_decidir) {
            if (k1.es_propio) {
                pie.innerHTML = '<div class="pg-aviso-propio">Registraste esta fabricación: el K1 lo tiene que decidir otro supervisor.</div>';
            }
            return;
        }
        if (k1.es_propio) {
            // Solo llega aquí un superusuario: puede decidir, pero se le avisa.
            pie.innerHTML = '<div class="pg-aviso-propio pg-aviso-propio--info">Registraste esta fabricación. ' +
                'Como administrador puedes decidirla; quedará registrado que la decisión fue tuya.</div>';
        }

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
        fetch(URL_DETALLE(grabadoActual), { headers: { 'Accept': 'application/json' } })
            .then(r => r.json())
            .then(res => {
                if (pedido !== pedidoActual) return;
                if (res.status !== 'ok') { mostrarError(res.message || 'No se pudo cargar el grabado.'); return; }
                renderizar(res.data);
            })
            .catch(() => { if (pedido === pedidoActual) mostrarError('Error de conexión al cargar el grabado.'); });
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
        construir();
        clearTimeout(temporizadorCierre);
        opcionesActuales = opciones || {};
        grabadoActual = grabadoId;
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
        if (panel && !panel.hidden && grabadoActual !== null) cargar();
    }

    window.PanelGrabado = { abrir, cerrar, recargar };
})();

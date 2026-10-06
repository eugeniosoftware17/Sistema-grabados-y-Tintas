/* ============================================================
   K1 Pendientes (supervisores) — ver views_grabados.py
   El reporte completo del grabado se muestra con el componente
   reutilizable PanelGrabado (static/js/componentes/panel_grabado.js).
   ============================================================ */

(function () {
    'use strict';

    const LARGO_MINIMO_MOTIVO = 5;
    let pruebas = [];
    let pruebaEnRechazo = null;

    const $ = (id) => document.getElementById(id);

    function getCookie(name) {
        const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function escapar(valor) {
        const div = document.createElement('div');
        div.textContent = valor === null || valor === undefined || valor === '' ? '—' : String(valor);
        return div.innerHTML;
    }

    function acciones(p) {
        const detalle = `<button type="button" class="boton boton--detalle" data-accion="detalle" data-id="${p.id}">Ver detalle</button>`;
        if (!p.puede_decidir) {
            return `<div class="k1-acciones">${detalle}</div>
                <span class="k1-propio" style="margin-top:5px;">Registraste esta fabricación: la tiene que decidir otro supervisor.</span>`;
        }
        return `
            <div class="k1-acciones">
                ${detalle}
                <button type="button" class="boton boton--aprobar" data-accion="aprobar" data-id="${p.id}">Aprobar</button>
                <button type="button" class="boton boton--rechazar" data-accion="rechazar" data-id="${p.id}">Rechazar</button>
            </div>`;
    }

    function renderizar() {
        const cuerpo = $('k1-cuerpo');
        $('k1-contador').textContent = `${pruebas.length} pendiente(s)`;
        if (!pruebas.length) {
            cuerpo.innerHTML = '<tr><td colspan="8" class="tabla-sin-resultados">No hay pruebas K1 pendientes.</td></tr>';
            return;
        }
        cuerpo.innerHTML = pruebas.map(p => `
            <tr class="k1-fila" tabindex="0" data-id="${p.id}"
                aria-label="Ver detalle del grabado ${escapar(p.of)} ${escapar(p.proceso)}">
                <td data-label="OF"><strong>${escapar(p.of)}</strong></td>
                <td data-label="Proceso">${escapar(p.proceso)}</td>
                <td data-label="Cliente">${escapar(p.cliente)}</td>
                <td data-label="Referencia" class="k1-referencia">${escapar(p.referencia)}</td>
                <td data-label="Máquina">${escapar(p.maquina)}</td>
                <td data-label="Intento">${escapar(p.intento)}</td>
                <td data-label="Registrado por">
                    <span>${escapar(p.registrado_por)}<br><small style="color:#666;">${escapar(p.registrado_el)}</small></span>
                </td>
                <td data-label="Acciones" class="k1-celda-acciones">${acciones(p)}</td>
            </tr>`).join('');
    }

    function cargar() {
        fetch('/grabados/api/k1/pendientes/')
            .then(r => r.json())
            .then(res => {
                if (res.status !== 'ok') {
                    $('k1-cuerpo').innerHTML = `<tr><td colspan="8" class="tabla-sin-resultados">${escapar(res.message)}</td></tr>`;
                    return;
                }
                pruebas = res.data;
                renderizar();
            })
            .catch(() => {
                $('k1-cuerpo').innerHTML = '<tr><td colspan="8" class="tabla-sin-resultados">Error de conexión al cargar los K1.</td></tr>';
            });
    }

    // ------------------------------------------------------------- decisiones
    function enviarDecision(id, accion, motivo) {
        return fetch(`/grabados/api/k1/${id}/${accion}/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify({ motivo: motivo || '' }),
        })
            .then(r => r.json())
            .then(res => {
                alert(res.status === 'ok' ? res.message : 'Error: ' + res.message);
                cargar();
                if (res.status === 'ok') window.PanelGrabado.cerrar();
                else window.PanelGrabado.recargar();
                return res.status === 'ok';
            })
            .catch(() => { alert('Error de conexión.'); return false; });
    }

    // Superusuario decidiendo sobre una fabricación que registró él mismo:
    // aviso propio con Confirmar / Cancelar. Devuelve una promesa con true/false.
    function confirmarAvisoPropio(verbo) {
        const modal = $('modal-aviso-propio');
        const btnConfirmar = $('aviso-propio-confirmar');
        const btnCancelar = $('aviso-propio-cancelar');
        $('aviso-propio-texto').textContent =
            `Estás ${verbo} una fabricación que registraste tú. Quedará registrado que la decisión fue tuya.`;
        modal.style.display = 'flex';
        btnCancelar.focus();

        return new Promise(resolve => {
            function terminar(valor) {
                modal.style.display = 'none';
                btnConfirmar.removeEventListener('click', alConfirmar);
                btnCancelar.removeEventListener('click', alCancelar);
                document.removeEventListener('keydown', alTeclear, true);
                resolve(valor);
            }
            function alConfirmar() { terminar(true); }
            function alCancelar() { terminar(false); }
            function alTeclear(e) {
                if (e.key === 'Escape') { e.stopPropagation(); terminar(false); }
            }
            btnConfirmar.addEventListener('click', alConfirmar);
            btnCancelar.addEventListener('click', alCancelar);
            document.addEventListener('keydown', alTeclear, true);
        });
    }

    // Recibe los datos mínimos del K1 (id, intento, of, proceso, maquina, es_propio)
    // desde la fila de la tabla o desde el panel de detalle.
    function aprobar(k1) {
        const confirmado = k1.es_propio
            ? confirmarAvisoPropio('aprobando')
            : Promise.resolve(confirm(`¿Aprobar el K1 (intento ${k1.intento}) del grabado ${k1.of} ${k1.proceso}?`));
        confirmado.then(ok => { if (ok) enviarDecision(k1.id, 'aprobar'); });
    }

    function abrirModalRechazo(k1) {
        pruebaEnRechazo = k1;
        $('rechazo-subtitulo').textContent = `Grabado ${k1.of} ${k1.proceso} — intento ${k1.intento} (${k1.maquina})`;
        $('rechazo-motivo').value = '';
        $('modal-rechazo').style.display = 'flex';
        $('rechazo-motivo').focus();
    }

    window.cerrarModalRechazo = function () {
        pruebaEnRechazo = null;
        $('modal-rechazo').style.display = 'none';
    };

    window.confirmarRechazo = function () {
        if (!pruebaEnRechazo) return;
        const motivo = $('rechazo-motivo').value.trim();
        if (motivo.length < LARGO_MINIMO_MOTIVO) {
            alert(`El motivo del rechazo es obligatorio (al menos ${LARGO_MINIMO_MOTIVO} caracteres).`);
            return;
        }
        const k1 = pruebaEnRechazo;
        const btn = $('rechazo-btn-confirmar');
        btn.disabled = true;
        (k1.es_propio ? confirmarAvisoPropio('rechazando') : Promise.resolve(true))
            .then(ok => ok ? enviarDecision(k1.id, 'rechazar', motivo) : false)
            .then(ok => { if (ok) window.cerrarModalRechazo(); })
            .finally(() => { btn.disabled = false; });
    };

    // ------------------------------------------------------------ panel detalle
    function desdePanel(k1, detalle) {
        return {
            id: k1.id, intento: k1.intento, maquina: k1.maquina, es_propio: k1.es_propio,
            of: detalle.of_origen, proceso: detalle.proceso,
        };
    }

    function verDetalle(p) {
        window.PanelGrabado.abrir(p.grabado_id, {
            acciones: {
                onAprobar: (k1, detalle) => aprobar(desdePanel(k1, detalle)),
                onRechazar: (k1, detalle) => abrirModalRechazo(desdePanel(k1, detalle)),
            },
        });
    }

    // ---------------------------------------------------------------- eventos
    const cuerpo = $('k1-cuerpo');

    cuerpo.addEventListener('click', (e) => {
        const fila = e.target.closest('.k1-fila');
        if (!fila) return;
        const p = pruebas.find(x => x.id === Number(fila.dataset.id));
        if (!p) return;

        const boton = e.target.closest('button[data-accion]');
        const accion = boton ? boton.dataset.accion : 'detalle';
        if (accion === 'aprobar') aprobar(p);
        else if (accion === 'rechazar') abrirModalRechazo(p);
        else verDetalle(p);
    });

    cuerpo.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' || !e.target.classList.contains('k1-fila')) return;
        const p = pruebas.find(x => x.id === Number(e.target.dataset.id));
        if (p) { e.preventDefault(); verDetalle(p); }
    });

    cargar();
})();

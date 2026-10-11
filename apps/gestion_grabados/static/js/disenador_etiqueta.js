/* Diseñador de la etiqueta 6x4 en el admin (ConfiguracionEtiquetaAdmin, solo superusuario).

   Fuente de verdad: el campo oculto campos_json ({clave: {visible, x, y, ancho, alto, tamano}},
   medidas en pulgadas y letra en pt). Arrastrar un campo en la vista previa o cambiar la
   tabla actualiza ese JSON y la vista previa; el servidor lo valida al guardar
   (services.etiquetas.normalizar_campos). */
(function () {
    'use strict';

    const oculto = document.getElementById('id_campos_json');
    const etiqueta = document.getElementById('etiqueta');
    const tabla = document.getElementById('de-campos');
    if (!oculto || !etiqueta || !tabla) return;

    const DEFECTOS = JSON.parse(document.getElementById('de-defectos').textContent);
    const LIM = JSON.parse(document.getElementById('de-limites').textContent);
    const PASO = 0.05;   // al arrastrar, se ajusta a 0.05"

    let campos;
    try { campos = JSON.parse(oculto.value); } catch (e) { campos = {}; }
    Object.keys(DEFECTOS).forEach((clave) => { campos[clave] = { ...DEFECTOS[clave], ...(campos[clave] || {}) }; });

    const redondear = (n, d = 2) => Math.round(n * 10 ** d) / 10 ** d;
    const acotar = (n, min, max) => Math.min(Math.max(n, min), max);
    const elemento = (clave) => etiqueta.querySelector(`.et-campo[data-campo="${clave}"]`);
    const filaTabla = (clave) => tabla.querySelector(`tr[data-campo="${clave}"]`);

    function guardarOculto() { oculto.value = JSON.stringify(campos); }

    function dibujar(clave) {
        const c = campos[clave];
        const el = elemento(clave);
        if (el) {
            el.hidden = !c.visible;
            el.style.left = `${c.x}in`;
            el.style.top = `${c.y}in`;
            el.style.width = `${c.ancho}in`;
            el.style.height = `${c.alto}in`;
            el.style.fontSize = `${c.tamano}pt`;
        }
        const fila = filaTabla(clave);
        fila.querySelectorAll('input[data-prop]').forEach((input) => {
            const prop = input.dataset.prop;
            if (prop === 'visible') input.checked = c.visible;
            else if (document.activeElement !== input) input.value = c[prop];
        });
    }

    function dibujarTodo() { Object.keys(campos).forEach(dibujar); guardarOculto(); }

    // Mantiene el campo dentro de la etiqueta (igual que el servidor).
    function ajustar(c) {
        c.ancho = redondear(acotar(c.ancho, LIM.medida_min, LIM.ancho));
        c.alto = redondear(acotar(c.alto, LIM.medida_min, LIM.alto));
        c.tamano = redondear(acotar(c.tamano, LIM.tamano_min, LIM.tamano_max), 1);
        c.x = redondear(acotar(c.x, 0, LIM.ancho - c.ancho));
        c.y = redondear(acotar(c.y, 0, LIM.alto - c.alto));
    }

    function seleccionar(clave) {
        etiqueta.querySelectorAll('.de-seleccionado').forEach((el) => el.classList.remove('de-seleccionado'));
        tabla.querySelectorAll('.de-seleccionado').forEach((el) => el.classList.remove('de-seleccionado'));
        const el = elemento(clave);
        if (el) el.classList.add('de-seleccionado');
        filaTabla(clave).classList.add('de-seleccionado');
    }

    // ------------------------------------------------------------ tabla
    tabla.addEventListener('input', (e) => {
        const input = e.target.closest('input[data-prop]');
        if (!input) return;
        const clave = input.closest('tr').dataset.campo;
        const c = campos[clave];
        if (input.dataset.prop === 'visible') {
            c.visible = input.checked;
        } else {
            const valor = parseFloat(input.value);
            if (Number.isNaN(valor)) return;
            c[input.dataset.prop] = valor;
        }
        ajustar(c);
        seleccionar(clave);
        dibujar(clave);
        guardarOculto();
    });
    // Al salir del campo se muestra el valor ya ajustado.
    tabla.addEventListener('focusout', (e) => {
        const input = e.target.closest('input[data-prop]');
        if (input) setTimeout(() => dibujar(input.closest('tr').dataset.campo));
    });
    tabla.addEventListener('click', (e) => {
        const nombre = e.target.closest('.de-nombre');
        if (nombre) seleccionar(nombre.closest('tr').dataset.campo);
    });

    // ------------------------------------------------- arrastrar en la vista previa
    let arrastre = null;
    etiqueta.addEventListener('pointerdown', (e) => {
        const el = e.target.closest('.et-campo');
        if (!el) return;
        e.preventDefault();
        const clave = el.dataset.campo;
        seleccionar(clave);
        const pxPorPulgada = etiqueta.getBoundingClientRect().width / LIM.ancho;
        arrastre = { clave, el, pxPorPulgada, inicioX: e.clientX, inicioY: e.clientY,
                     x: campos[clave].x, y: campos[clave].y };
        el.setPointerCapture(e.pointerId);
    });
    etiqueta.addEventListener('pointermove', (e) => {
        if (!arrastre) return;
        const c = campos[arrastre.clave];
        const dx = (e.clientX - arrastre.inicioX) / arrastre.pxPorPulgada;
        const dy = (e.clientY - arrastre.inicioY) / arrastre.pxPorPulgada;
        c.x = Math.round((arrastre.x + dx) / PASO) * PASO;
        c.y = Math.round((arrastre.y + dy) / PASO) * PASO;
        ajustar(c);
        dibujar(arrastre.clave);
    });
    const soltar = () => { if (arrastre) { arrastre = null; guardarOculto(); } };
    etiqueta.addEventListener('pointerup', soltar);
    etiqueta.addEventListener('pointercancel', soltar);

    // ---------------------------------------------------------------- otros
    document.getElementById('de-restaurar').addEventListener('click', () => {
        Object.keys(DEFECTOS).forEach((clave) => { campos[clave] = { ...DEFECTOS[clave] }; });
        dibujarTodo();
    });

    // Vista previa del logo elegido (todavía sin guardar).
    const archivoLogo = document.getElementById('id_logo_archivo');
    const imagenLogo = etiqueta.querySelector('.et-logo img');
    if (archivoLogo && imagenLogo) {
        archivoLogo.addEventListener('change', () => {
            const archivo = archivoLogo.files[0];
            if (!archivo) return;
            const lector = new FileReader();
            lector.onload = () => { imagenLogo.src = lector.result; };
            lector.readAsDataURL(archivo);
        });
    }

    dibujarTodo();
})();
